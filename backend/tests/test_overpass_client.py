import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from app.models import OsmBranchCache, OverpassFetchLog
from app.services.brand_matcher import BrandMatcher
from app.services.hybrid_rag import nearby_branches
from app.services.overpass_client import (
    GRID_PAD_M,
    BranchDiscoveryService,
    OverpassClient,
    OverpassUnavailableError,
    build_query,
    circle_contains,
    haversine_km,
    parse_overpass_elements,
    snap_to_grid,
)
from tests.conftest import SEED_SLUGS, UPM_LAT, UPM_LNG, add_branch, point_north

UA = "CalCipher-UPM-Academic-Project/1.0"
MIRRORS = ["https://overpass.test/a/interpreter", "https://overpass.test/b/interpreter"]

SAMPLE_PAYLOAD = {
    "version": 0.6,
    "elements": [
        {"type": "node", "id": 101, "lat": 14.5768, "lon": 120.9884,
         "tags": {"amenity": "fast_food", "name": "Jollibee Taft", "brand": "Jollibee",
                  "addr:street": "Taft Avenue", "addr:city": "Manila"}},
        {"type": "way", "id": 202, "center": {"lat": 14.5770, "lon": 120.9902},
         "tags": {"amenity": "restaurant", "name": "Andok's Litson Manok - Pedro Gil"}},
        {"type": "relation", "id": 303, "center": {"lat": 14.5730, "lon": 120.9905},
         "tags": {"amenity": "restaurant", "name": "24 Chicken Leon Guinto"}},
        {"type": "node", "id": 404, "lat": 14.5792, "lon": 120.9862,
         "tags": {"amenity": "cafe", "name": "Starbucks Padre Faura"}},
        # brand only, no name -> brand becomes the display name
        {"type": "node", "id": 505, "lat": 14.5800, "lon": 120.9870,
         "tags": {"amenity": "fast_food", "brand": "Mang Inasal"}},
        # --- elements that must be skipped ---
        {"type": "node", "id": 601, "tags": {"amenity": "cafe", "name": "No Coordinates"}},
        {"type": "way", "id": 602, "tags": {"amenity": "cafe", "name": "Way Without Center"}},
        {"type": "node", "id": 603, "lat": 35.68, "lon": 139.76, "tags": {"name": "Jollibee Tokyo"}},
        {"type": "node", "id": 604, "lat": 14.58, "lon": 120.98, "tags": {"amenity": "cafe"}},
        {"type": "node", "id": 605, "lat": "14.58", "lon": 120.98, "tags": {"name": "String Lat"}},
        {"type": "area", "id": 606, "tags": {"name": "Not a POI"}},
        # duplicate of node 101 -> deduplicated
        {"type": "node", "id": 101, "lat": 14.5768, "lon": 120.9884, "tags": {"name": "Jollibee Taft"}},
    ],
}


def _ok(payload=SAMPLE_PAYLOAD) -> httpx.Response:
    return httpx.Response(200, json=payload)


class Recorder:
    """httpx MockTransport handler that replays scripted responses per URL."""

    def __init__(self, script):
        self.script = script  # url -> Response | Exception | callable
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        action = self.script[str(request.url)]
        if isinstance(action, Exception):
            raise action
        return action() if callable(action) else action

    @property
    def urls(self):
        return [str(r.url) for r in self.requests]


def make_client(script) -> tuple[OverpassClient, Recorder]:
    rec = Recorder(script)
    return OverpassClient(MIRRORS, user_agent=UA, timeout_s=5, transport=httpx.MockTransport(rec)), rec


# ---------------------------------------------------------------------------
# Query + parsing
# ---------------------------------------------------------------------------


def test_build_query_matches_contract():
    assert build_query(14.5785, 120.985, 3000) == (
        '[out:json][timeout:15];('
        'nwr["amenity"~"restaurant|fast_food|cafe"](around:3000,14.578500,120.985000);'
        'nwr["shop"="convenience"]["brand"~"7[ -]?eleven",i](around:3000,14.578500,120.985000);'
        'nwr["shop"="convenience"]["name"~"7[ -]?eleven",i](around:3000,14.578500,120.985000);'
        ");out center;"
    )


def test_build_query_rejects_non_finite_values():
    with pytest.raises(ValueError):
        build_query(float("nan"), 120.98, 1000)
    with pytest.raises(ValueError):
        build_query(14.5, 120.98, 0)


def test_parse_node_way_relation_coordinates():
    pois = {(p.osm_type, p.osm_id): p for p in parse_overpass_elements(SAMPLE_PAYLOAD)}
    assert set(pois) == {("node", 101), ("way", 202), ("relation", 303), ("node", 404), ("node", 505)}

    node = pois[("node", 101)]
    assert (node.latitude, node.longitude) == (14.5768, 120.9884)
    assert node.brand_tag == "Jollibee"
    assert node.address_hint == "Taft Avenue, Manila"

    way = pois[("way", 202)]
    assert (way.latitude, way.longitude) == (14.5770, 120.9902)  # from `center`
    assert way.amenity_type == "restaurant"

    assert pois[("relation", 303)].latitude == 14.5730
    assert pois[("node", 505)].poi_name == "Mang Inasal"


def test_parse_tolerates_garbage_payloads():
    assert parse_overpass_elements({}) == []
    assert parse_overpass_elements({"elements": None}) == []
    assert parse_overpass_elements({"elements": ["junk", 42, {"type": "node"}]}) == []


# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------


async def test_fetch_sends_descriptive_user_agent_and_post_body():
    client, rec = make_client({MIRRORS[0]: _ok})
    pois = await client.fetch_pois(14.5785, 120.985, 1500)
    assert len(pois) == 5
    req = rec.requests[0]
    assert req.method == "POST"
    assert req.headers["User-Agent"] == UA
    body = parse_qs(req.content.decode())
    assert body["data"] == [build_query(14.5785, 120.985, 1500)]


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_falls_back_to_mirror_on_overload(status):
    client, rec = make_client({MIRRORS[0]: httpx.Response(status), MIRRORS[1]: _ok})
    pois = await client.fetch_pois(14.5785, 120.985, 1500)
    assert len(pois) == 5
    assert rec.urls == MIRRORS


async def test_falls_back_to_mirror_on_timeout():
    client, rec = make_client({MIRRORS[0]: httpx.ReadTimeout("slow"), MIRRORS[1]: _ok})
    assert len(await client.fetch_pois(14.5785, 120.985, 1500)) == 5
    assert rec.urls == MIRRORS


async def test_all_mirrors_rate_limited_raises():
    client, _ = make_client({MIRRORS[0]: httpx.Response(429), MIRRORS[1]: httpx.Response(504)})
    with pytest.raises(OverpassUnavailableError) as err:
        await client.fetch_pois(14.5785, 120.985, 1500)
    assert "HTTP 429" in str(err.value) and "HTTP 504" in str(err.value)


async def test_bad_request_does_not_hammer_mirrors():
    client, rec = make_client({MIRRORS[0]: httpx.Response(400), MIRRORS[1]: _ok})
    with pytest.raises(OverpassUnavailableError):
        await client.fetch_pois(14.5785, 120.985, 1500)
    assert rec.urls == MIRRORS[:1]


async def test_runtime_error_remark_is_treated_as_failure():
    partial = {"elements": SAMPLE_PAYLOAD["elements"][:1],
               "remark": "runtime error: Query timed out in \"query\" at line 1 after 15 seconds."}
    client, rec = make_client({MIRRORS[0]: _ok(partial), MIRRORS[1]: _ok})
    assert len(await client.fetch_pois(14.5785, 120.985, 1500)) == 5  # partial result rejected
    assert rec.urls == MIRRORS


async def test_invalid_json_is_treated_as_failure():
    client, _ = make_client({
        MIRRORS[0]: httpx.Response(200, content=b"<html>busy</html>"),
        MIRRORS[1]: httpx.Response(200, content=json.dumps({"no": "elements"}).encode()),
    })
    with pytest.raises(OverpassUnavailableError):
        await client.fetch_pois(14.5785, 120.985, 1500)


async def test_semaphore_caps_concurrency_at_one():
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return _ok()

    client = OverpassClient(MIRRORS, user_agent=UA, transport=httpx.MockTransport(handler))
    await asyncio.gather(*(client.fetch_pois(14.5785, 120.985, 1000) for _ in range(4)))
    assert peak == 1


# ---------------------------------------------------------------------------
# Grid cache geometry
# ---------------------------------------------------------------------------


def test_snap_to_grid_and_padding_cover_whole_cell():
    c_lat, c_lng, key = snap_to_grid(14.5785, 120.9850)
    assert key == "1457:12098"
    # Every corner of the cell is within GRID_PAD_M of the snapped center.
    for corner in ((14.57, 120.98), (14.58, 120.98), (14.57, 120.99), (14.58, 120.99)):
        assert haversine_km(c_lat, c_lng, *corner) * 1000 < GRID_PAD_M


def test_circle_contains():
    assert circle_contains(14.5785, 120.985, 3800, 14.5785, 120.985, 3000)
    north = point_north(14.5785, 120.985, 1.0)
    assert circle_contains(14.5785, 120.985, 4100, *north, 3000)
    assert not circle_contains(14.5785, 120.985, 3900, *north, 3000)


# ---------------------------------------------------------------------------
# Discovery service + PostgreSQL cache
# ---------------------------------------------------------------------------


async def _matcher(_session):
    return BrandMatcher(SEED_SLUGS)


def make_service(script, *, enabled=True):
    client, rec = make_client(script)
    return BranchDiscoveryService(client, _matcher, ttl_hours=24, max_radius_m=5000, enabled=enabled), rec


@pytest.mark.db
async def test_live_fetch_upserts_matches_and_logs(db_session):
    service, rec = make_service({MIRRORS[0]: _ok})
    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5)

    assert result.source == "live" and not result.cache_hit
    assert result.fetched_count == 5
    assert result.matched_count == 4  # Jollibee, Andok's, 24 Chicken, Mang Inasal (brand tag)
    rows = {
        (r.osm_type, r.osm_id): r
        for r in (await db_session.execute(select(OsmBranchCache))).scalars()
    }
    assert rows[("node", 101)].matched_brand_slug == "jollibee"
    assert rows[("way", 202)].matched_brand_slug == "andoks"
    assert rows[("relation", 303)].matched_brand_slug == "24 chicken"
    assert rows[("node", 404)].matched_brand_slug is None  # Starbucks: no calorie data
    log = (await db_session.execute(select(OverpassFetchLog))).scalar_one()
    assert log.radius_m == 1500 + GRID_PAD_M and log.element_count == 5
    assert len(rec.requests) == 1


@pytest.mark.db
async def test_second_request_in_same_area_is_a_cache_hit(db_session):
    service, rec = make_service({MIRRORS[0]: _ok})
    await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5)
    nearby = point_north(UPM_LAT, UPM_LNG, 0.3)
    again = await service.ensure_branches(db_session, *nearby, 1.0)
    assert again.source == "cache" and again.cache_hit
    assert len(rec.requests) == 1  # Overpass was not called a second time


@pytest.mark.db
async def test_expired_cache_triggers_refetch(db_session):
    service, rec = make_service({MIRRORS[0]: _ok})
    db_session.add(OverpassFetchLog(
        grid_key="food-v2:x", center_lat=UPM_LAT, center_lng=UPM_LNG, radius_m=5000, element_count=0,
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=25),
    ))
    await db_session.commit()
    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.0)
    assert result.source == "live" and len(rec.requests) == 1


@pytest.mark.db
async def test_expired_fetch_still_counts_as_saved_coverage_for_fallback_decisions(db_session):
    service, _ = make_service({MIRRORS[0]: _ok})
    assert not await service.is_covered(db_session, UPM_LAT, UPM_LNG, 1000, fresh_only=False)
    db_session.add(OverpassFetchLog(
        grid_key="food-v2:x", center_lat=UPM_LAT, center_lng=UPM_LNG, radius_m=5000, element_count=0,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=30),
    ))
    await db_session.commit()
    assert not await service.is_covered(db_session, UPM_LAT, UPM_LNG, 1000)
    assert await service.is_covered(db_session, UPM_LAT, UPM_LNG, 1000, fresh_only=False)


@pytest.mark.db
async def test_old_restaurant_only_coverage_refetches_to_include_catalog_food_stores(db_session):
    service, rec = make_service({MIRRORS[0]: _ok})
    db_session.add(OverpassFetchLog(
        grid_key="legacy-restaurants", center_lat=UPM_LAT, center_lng=UPM_LNG,
        radius_m=5000, element_count=0, fetched_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    assert (await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1)).source == "live"
    assert (await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1)).source == "cache"
    assert len(rec.requests) == 1


@pytest.mark.db
@pytest.mark.parametrize("status", [429, 504])
async def test_overpass_failure_falls_back_to_cached_branches(db_session, status):
    await add_branch(db_session, osm_id=9001, poi_name="Jollibee Taft", lat=14.5768, lng=120.9884,
                     slug="jollibee", fetched_at=datetime.now(timezone.utc) - timedelta(days=3))
    service, rec = make_service({MIRRORS[0]: httpx.Response(status), MIRRORS[1]: httpx.Response(status)})

    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5)
    assert result.source == "stale_cache_fallback"
    assert f"HTTP {status}" in result.error
    assert len(rec.requests) == 2  # both mirrors tried

    pins = await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=1.5, include_unmatched=True)
    assert [p.poi_name for p in pins] == ["Jollibee Taft"]
    # a failed fetch must not be recorded as coverage
    assert (await db_session.execute(select(func.count()).select_from(OverpassFetchLog))).scalar_one() == 0


@pytest.mark.db
async def test_live_fetch_purges_seed_rows_only_inside_fetched_area(db_session):
    await add_branch(db_session, osm_type="seed", osm_id=1, poi_name="Jollibee Taft - Pedro Gil",
                     lat=14.5768, lng=120.9884, slug="jollibee")
    far = point_north(UPM_LAT, UPM_LNG, 20)  # Quezon City-ish, far outside the fetch radius
    await add_branch(db_session, osm_type="seed", osm_id=2, poi_name="Jollibee Far", lat=far[0],
                     lng=far[1], slug="jollibee")
    service, _ = make_service({MIRRORS[0]: _ok})
    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5)
    assert result.purged_seed_rows == 1
    seeds = (await db_session.execute(
        select(OsmBranchCache.osm_id).where(OsmBranchCache.osm_type == "seed")
    )).scalars().all()
    assert seeds == [2]


@pytest.mark.db
async def test_disabled_overpass_uses_cache_only(db_session):
    service, rec = make_service({MIRRORS[0]: _ok}, enabled=False)
    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.0)
    assert result.source == "disabled" and rec.requests == []


@pytest.mark.db
async def test_upsert_refreshes_existing_rows(db_session):
    await add_branch(db_session, osm_id=101, poi_name="Old Name", lat=14.5, lng=120.9, slug=None)
    service, _ = make_service({MIRRORS[0]: _ok})
    await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5)
    row = await db_session.get(OsmBranchCache, ("node", 101))
    await db_session.refresh(row)
    assert row.poi_name == "Jollibee Taft" and row.matched_brand_slug == "jollibee"


@pytest.mark.db
async def test_failure_opens_circuit_breaker(db_session):
    service, rec = make_service({MIRRORS[0]: httpx.Response(504), MIRRORS[1]: httpx.Response(504)})
    first = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.0)
    second = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.0)
    assert first.source == second.source == "stale_cache_fallback"
    assert len(rec.requests) == 2  # second call did not retry the mirrors
    assert "cooldown" in second.error

    service._cooldown_until = 0.0  # cooldown elapsed -> Overpass is tried again
    rec.script = {MIRRORS[0]: _ok}
    third = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.0)
    assert third.source == "live"


def test_query_server_limit_follows_client_wait():
    from app.services.overpass_client import server_timeout_for
    assert server_timeout_for(8) == 15 and server_timeout_for(20) == 15
    assert server_timeout_for(45) == 40
    assert build_query(14.5785, 120.985, 3000, server_timeout_for(45)).startswith("[out:json][timeout:40];(")


@pytest.mark.db
async def test_device_location_lookup_writes_nothing(db_session):
    service, rec = make_service({MIRRORS[0]: _ok})
    result = await service.ensure_branches(db_session, UPM_LAT, UPM_LNG, 1.5, persist=False)

    assert result.source == "live" and result.fetched_count == 5 and result.matched_count == 4
    assert {p["osm_id"] for p in result.places} == {101, 202, 303, 404, 505}
    assert all(p["place_id"] is None and "last_fetched_at" not in p for p in result.places)
    assert (await db_session.execute(select(OsmBranchCache))).first() is None
    assert (await db_session.execute(select(OverpassFetchLog))).first() is None
    assert len(rec.requests) == 1
