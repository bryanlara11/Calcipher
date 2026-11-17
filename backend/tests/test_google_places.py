import json

import httpx
import pytest
from pydantic import SecretStr

from app.schemas import SearchConstraints
from app.services.brand_matcher import BrandMatcher
from app.services.google_places import GooglePlacesDiscovery, PlacesUnavailable
from app.services.hybrid_rag import nearby_branches, retrieve
from tests.conftest import add_branch, add_item


@pytest.fixture
def places_client():
    async def matcher(session):
        return BrandMatcher(["mcdonalds"])

    def google(request):
        assert request.headers["X-Goog-Api-Key"] == "test"
        payload = json.loads(request.content)
        if request.url.path.endswith(":searchText"):
            assert payload["strictTypeFiltering"] and payload["includedType"] == "restaurant"
            assert payload["textQuery"] == "mcdonalds"
        else:
            assert request.url.path.endswith(":searchNearby")
            assert payload["includedPrimaryTypes"] == ["restaurant"]
            assert payload["locationRestriction"]["circle"]["radius"] == 2000
        assert "places.userRatingCount" in request.headers["X-Goog-FieldMask"].split(",")
        restaurant = {
            "primaryType": "fast_food_restaurant", "displayName": {"text": "McDonald's"},
            "location": {"latitude": 14.582, "longitude": 120.986},
        }
        return httpx.Response(200, json={"places": [
            {**restaurant, "id": "four", "userRatingCount": 4},
            {**restaurant, "id": "unknown"},
            {**restaurant, "id": "test-place", "userRatingCount": 5},
            {**restaurant, "id": "cafe", "primaryType": "cafe", "userRatingCount": 20},
        ]})

    return GooglePlacesDiscovery(SecretStr("test"), matcher, transport=httpx.MockTransport(google))


@pytest.mark.parametrize("brand", [None, "mcdonalds"])
async def test_live_place_identity(places_client, brand):
    discovery = await places_client.ensure_branches(None, 14.582, 120.986, 2, brand=brand)
    assert discovery.fetched_count == discovery.matched_count == 1
    assert discovery.places[0]["place_id"] == "test-place"


async def test_missing_key():
    with pytest.raises(PlacesUnavailable, match="GOOGLE_PLACES_API_KEY"):
        await GooglePlacesDiscovery(None, None).ensure_branches(None, 14.582, 120.986, 2)


async def test_live_map_and_retrieval_ignore_old_branches(places_client, db_session, embedder):
    await add_item(db_session, brand_name="McDonald's", brand_slug="mcdonalds", food_item="Chicken", calories=526)
    discovery = await places_client.ensure_branches(db_session, 14.582, 120.986, 2)
    point = {"lat": 14.582, "lng": 120.986}
    await add_branch(db_session, osm_id=1, poi_name="Old branch", slug="mcdonalds", **point)
    pins = await nearby_branches(db_session, **point, radius_km=2, include_unmatched=True, places=discovery.places)
    assert [pin.place_id for pin in pins] == ["test-place"]
    assert pins[0].item_count == 1
    result = await retrieve(db_session, **point, constraints=SearchConstraints(max_calories=600),
                            query_vec=embedder.embed_one("chicken"), places=discovery.places)
    assert [(item.place_id, item.calories) for item in result.items] == [("test-place", 526)]
    assert not await nearby_branches(db_session, **point, radius_km=2, include_unmatched=True, places=[])

async def test_map_uses_one_visible_area_search_within_original_radius():
    calls = []

    async def matcher(session):
        return BrandMatcher(["mcdonalds"])

    def place(place_id, lat, reviews=9):
        return {"id": place_id, "displayName": {"text": "McDonald's"}, "primaryType": "restaurant",
                "location": {"latitude": lat, "longitude": 120.985}, "userRatingCount": reviews}

    def google(request):
        payload = json.loads(request.content)
        calls.append(payload)
        center = payload["locationRestriction"]["circle"]["center"]
        assert 14.580 < center["latitude"] < 14.583
        rows = [place("un-taft", 14.582), place("outside", 14.64)]
        rows.extend(place(str(i), 14.581, reviews=1) for i in range(18))
        return httpx.Response(200, json={"places": rows})

    discovery = GooglePlacesDiscovery(SecretStr("test"), matcher, transport=httpx.MockTransport(google))
    result = await discovery.ensure_branches(None, 14.5785, 120.985, 5, spread_map=True,
                                             viewport=[14.580, 14.583, 120.984, 120.986])
    assert len(calls) == 1
    assert [row["place_id"] for row in result.places] == ["un-taft"]


async def test_quota_cooldown_shared_by_map_and_chat():
    calls = []
    def google(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    discovery = GooglePlacesDiscovery(SecretStr("test"), None, transport=httpx.MockTransport(google))
    for brand in [None, "mcdonalds"]:
        with pytest.raises(PlacesUnavailable, match="usage limit"):
            await discovery.ensure_branches(None, 14.582, 120.986, 2, brand=brand)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Quota protection: how many requests actually reach Google
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def counting_google(responses=None, delay=0.0):
    """Mock Places endpoint; `responses` is a list consumed in order (last one repeats)."""
    calls = []
    script = list(responses or [])

    async def google(request):
        calls.append(json.loads(request.content))
        if delay:
            await asyncio.sleep(delay)
        if script:
            status, headers = script.pop(0) if len(script) > 1 else script[0]
        else:
            status, headers = 200, {}
        if status != 200:
            return httpx.Response(status, headers=headers)
        return httpx.Response(200, json={"places": [{
            "id": "p1", "displayName": {"text": "McDonald's"}, "primaryType": "fast_food_restaurant",
            "location": {"latitude": 14.582, "longitude": 120.986}, "userRatingCount": 50}]})

    return google, calls


async def _matcher(session):
    return BrandMatcher(["mcdonalds"])


def make_discovery(google, clock, **kwargs):
    return GooglePlacesDiscovery(SecretStr("test"), _matcher, transport=httpx.MockTransport(google),
                                 clock=clock, **kwargs)


async def test_repeat_search_is_served_from_cache_until_ttl():
    google, calls = counting_google()
    clock = Clock()
    d = make_discovery(google, clock, cache_ttl_s=300)
    first = await d.ensure_branches(None, 14.582, 120.986, 2)
    again = await d.ensure_branches(None, 14.58201, 120.98601, 2)  # ~1 m pin nudge: same area
    assert (first.source, again.source, again.cache_hit) == ("live", "cache", True)
    assert again.places == first.places and len(calls) == 1
    await d.ensure_branches(None, 14.582, 120.986, 3)  # different radius: a real new search
    assert len(calls) == 2
    clock.now += 301  # TTL expired
    assert (await d.ensure_branches(None, 14.582, 120.986, 2)).source == "live"
    assert len(calls) == 3


async def test_cache_can_be_disabled():
    google, calls = counting_google()
    d = make_discovery(google, Clock(), cache_ttl_s=0)
    for _ in range(3):
        await d.ensure_branches(None, 14.582, 120.986, 2)
    assert len(calls) == 3


async def test_concurrent_identical_searches_share_one_request():
    google, calls = counting_google(delay=0.05)
    d = make_discovery(google, Clock(), cache_ttl_s=0)
    results = await asyncio.gather(*(d.ensure_branches(None, 14.582, 120.986, 2) for _ in range(5)))
    assert len(calls) == 1
    assert all(r.places == results[0].places for r in results)


async def test_429_without_retry_after_backs_off_exponentially_and_resets_on_success():
    google, calls = counting_google([(429, {}), (429, {}), (200, {})])
    clock = Clock()
    d = make_discovery(google, clock, cache_ttl_s=0)

    with pytest.raises(PlacesUnavailable, match="60 seconds"):
        await d.ensure_branches(None, 14.582, 120.986, 2)
    for _ in range(5):  # app keeps asking during the cooldown: Google is not called
        with pytest.raises(PlacesUnavailable, match="usage limit"):
            await d.ensure_branches(None, 14.582, 120.986, 2)
    assert len(calls) == 1

    clock.now += 61  # cooldown over: exactly one probe, which is refused again -> 120 s
    with pytest.raises(PlacesUnavailable, match="120 seconds"):
        await d.ensure_branches(None, 14.582, 120.986, 2)
    assert len(calls) == 2

    clock.now += 121
    assert (await d.ensure_branches(None, 14.582, 120.986, 2)).source == "live"
    assert d.strikes == 0 and len(calls) == 3


async def test_backoff_is_capped():
    google, _ = counting_google([(429, {})])
    clock = Clock()
    d = make_discovery(google, clock, cache_ttl_s=0)
    d.strikes = 20
    with pytest.raises(PlacesUnavailable, match="1800 seconds"):
        await d.ensure_branches(None, 14.582, 120.986, 2)


async def test_cooldown_survives_an_api_restart(tmp_path):
    state = tmp_path / "places_cooldown.json"
    google, calls = counting_google([(429, {"Retry-After": "600"})])
    clock = Clock()
    d = make_discovery(google, clock, state_path=state)
    with pytest.raises(PlacesUnavailable):
        await d.ensure_branches(None, 14.582, 120.986, 2)

    restarted = make_discovery(google, clock, state_path=state)  # new process, same file
    clock.now += 300
    with pytest.raises(PlacesUnavailable, match="300 seconds"):
        await restarted.ensure_branches(None, 14.582, 120.986, 2)
    assert len(calls) == 1  # the restart did not re-hit Google


async def test_other_errors_pause_briefly_instead_of_retrying_every_request():
    google, calls = counting_google([(500, {}), (200, {})])
    clock = Clock()
    d = make_discovery(google, clock, cache_ttl_s=0)
    with pytest.raises(PlacesUnavailable, match="HTTP 500"):
        await d.ensure_branches(None, 14.582, 120.986, 2)
    with pytest.raises(PlacesUnavailable, match="resume in 30 seconds") as err:
        await d.ensure_branches(None, 14.582, 120.986, 2)
    assert err.value.seconds == 30 and len(calls) == 1
    clock.now += 31
    await d.ensure_branches(None, 14.582, 120.986, 2)
    assert len(calls) == 2


async def test_cached_results_still_served_during_cooldown():
    google, calls = counting_google([(200, {}), (429, {})])
    clock = Clock()
    d = make_discovery(google, clock, cache_ttl_s=300)
    await d.ensure_branches(None, 14.582, 120.986, 2)
    with pytest.raises(PlacesUnavailable):
        await d.ensure_branches(None, 14.6, 121.0, 2)  # new area -> 429
    cached = await d.ensure_branches(None, 14.582, 120.986, 2)  # known area still works
    assert cached.source == "cache" and len(calls) == 2
