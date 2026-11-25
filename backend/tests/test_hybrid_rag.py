import math

import httpx
import pytest
from sqlalchemy import func, select, text

from app.db.session import get_session
from app.main import create_app
from app.models import NutritionCatalog
from app.schemas import SearchConstraints
from app.services.brand_matcher import BrandMatcher, BrandRegistry
from app.services.embedder import HashingEmbedder, to_pgvector_literal
from app.services.generator import GroundedGenerator
from app.services.hybrid_rag import (
    RETRIEVAL_SQL,
    HybridRagService,
    nearby_branches,
    relaxation_ladder,
    retrieve,
)
from app.services.overpass_client import BranchDiscoveryService, bounding_box, haversine_km
from app.services.query_parser import QueryParser
from tests.conftest import SEED_SLUGS, UPM_LAT, UPM_LNG, add_branch, add_item, point_north

EMB = HashingEmbedder()


def vec(q: str) -> list[float]:
    return EMB.embed_one(q)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_haversine_reference_distances():
    # One degree of latitude on a sphere of radius 6371.0088 km.
    assert haversine_km(14.0, 121.0, 15.0, 121.0) == pytest.approx(111.195, abs=0.001)
    assert haversine_km(UPM_LAT, UPM_LNG, UPM_LAT, UPM_LNG) == 0.0
    # Symmetric
    a, b = (14.5785, 120.9850), (14.5547, 121.0244)
    assert haversine_km(*a, *b) == pytest.approx(haversine_km(*b, *a))


def test_bounding_box_encloses_circle():
    min_lat, max_lat, min_lng, max_lng = bounding_box(UPM_LAT, UPM_LNG, 3.0)
    for bearing in range(0, 360, 15):
        d = 3.0 / 6371.0088
        lat2 = math.degrees(math.asin(
            math.sin(math.radians(UPM_LAT)) * math.cos(d)
            + math.cos(math.radians(UPM_LAT)) * math.sin(d) * math.cos(math.radians(bearing))
        ))
        lng2 = UPM_LNG + math.degrees(math.atan2(
            math.sin(math.radians(bearing)) * math.sin(d) * math.cos(math.radians(UPM_LAT)),
            math.cos(d) - math.sin(math.radians(UPM_LAT)) * math.sin(math.radians(lat2)),
        ))
        assert min_lat <= lat2 <= max_lat and min_lng <= lng2 <= max_lng


def test_explicit_constraints_never_relax():
    c = SearchConstraints(max_calories=500, min_protein_g=20, max_price_php=150, max_distance_km=1)
    assert relaxation_ladder(c) == []
    assert c.max_calories == 500 and c.max_price_php == 150 and c.max_distance_km == 1


def test_vector_literal_validation():
    assert to_pgvector_literal([0.0] * 768).startswith("[0.0000000,")
    with pytest.raises(ValueError):
        to_pgvector_literal([0.0] * 10)
    with pytest.raises(ValueError):
        to_pgvector_literal([float("nan")] * 768)


def test_retrieval_sql_is_constant_and_parameterized():
    sql = str(RETRIEVAL_SQL)
    for param in (":user_lat", ":user_lng", ":max_cal", ":min_protein", ":target_brand", ":query_vec",
                  ":max_distance_km", ":k"):
        assert param in sql
    assert "%" not in sql and "{" not in sql  # no leftover string formatting


# ---------------------------------------------------------------------------
# Spatial join against PostgreSQL + pgvector
# ---------------------------------------------------------------------------


async def _fixture_world(session):
    """Branches due north of UP Manila at exact great-circle distances."""
    await add_item(session, brand_name="Mang Inasal", brand_slug="mang inasal",
                   food_item="Chicken Inasal Pecho without Rice", calories=360, protein_g=45, carbs_g=3, fat_g=18.5)
    await add_item(session, brand_name="Mang Inasal", brand_slug="mang inasal",
                   food_item="Pork BBQ with Rice", calories=590, protein_g=26, carbs_g=62, fat_g=26)
    await add_item(session, brand_name="Jollibee", brand_slug="jollibee",
                   food_item="Jolly Spaghetti", calories=480, protein_g=15, carbs_g=70, fat_g=15.5)
    await add_item(session, brand_name="Jollibee", brand_slug="jollibee",
                   food_item="Yumburger", calories=280, protein_g=12, carbs_g=32, fat_g=11.5)
    await add_item(session, brand_name="Subway", brand_slug="subway",
                   food_item="Roasted Chicken Sub", calories=320, protein_g=23, carbs_g=45, fat_g=5)
    await add_item(session, brand_name="Kenny Rogers Roasters", brand_slug="kenny rogers roasters",
                   food_item="Skinless Roast Chicken Breast Quarter", calories=290, protein_g=46, carbs_g=1, fat_g=11)

    for osm_id, name, slug, km in [
        (1, "Mang Inasal Taft", "mang inasal", 0.5),
        (2, "Mang Inasal Far", "mang inasal", 2.5),       # same brand, farther: must not duplicate items
        (3, "Jollibee Pedro Gil", "jollibee", 2.9),       # inside 3 km
        (4, "Subway Ermita", "subway", 3.2),               # just outside 3 km
        (5, "Kenny Rogers Malate", "kenny rogers roasters", 4.5),  # only reachable after relaxation
        (6, "Starbucks Padre Faura", None, 0.2),           # unmatched OSM POI
    ]:
        lat, lng = point_north(UPM_LAT, UPM_LNG, km)
        await add_branch(session, osm_id=osm_id, poi_name=name, lat=lat, lng=lng, slug=slug)


@pytest.mark.db
async def test_join_respects_radius_and_returns_distinct_items(db_session):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(max_distance_km=3.0), query_vec=vec("meal"), k=10)
    names = {i.food_item for i in res.items}
    assert names == {"Chicken Inasal Pecho without Rice", "Pork BBQ with Rice", "Jolly Spaghetti", "Yumburger"}
    assert res.candidate_count == 4
    assert len(res.items) == len({i.item_id for i in res.items})  # DISTINCT ON item
    for item in res.items:
        assert item.distance_km <= 3.0
        if item.brand_slug == "mang inasal":
            assert item.poi_name == "Mang Inasal Taft"  # nearest branch chosen


@pytest.mark.db
async def test_sql_haversine_matches_python(db_session):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(max_distance_km=3.0), query_vec=vec("x"), k=10)
    by_branch = {i.poi_name: i for i in res.items}
    assert by_branch["Mang Inasal Taft"].distance_km == pytest.approx(0.5, abs=1e-3)
    assert by_branch["Jollibee Pedro Gil"].distance_km == pytest.approx(2.9, abs=1e-3)
    for i in res.items:
        assert i.distance_km == pytest.approx(haversine_km(UPM_LAT, UPM_LNG, i.latitude, i.longitude), abs=1e-3)


@pytest.mark.db
@pytest.mark.parametrize("ceiling", [280, 300, 360, 479, 480, 600])
async def test_hard_calorie_ceiling(db_session, ceiling):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(max_calories=ceiling, max_distance_km=3.0),
                         query_vec=vec("meal"), k=10)
    expected = {c for c in (360, 590, 480, 280) if c <= ceiling}
    assert {i.calories for i in res.items} == expected


@pytest.mark.db
async def test_protein_floor_and_brand_filter(db_session):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(min_protein_g=25, max_distance_km=3.0),
                         query_vec=vec("meal"), k=10)
    assert {i.food_item for i in res.items} == {"Chicken Inasal Pecho without Rice", "Pork BBQ with Rice"}

    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(target_brand="jollibee", max_distance_km=3.0),
                         query_vec=vec("meal"), k=10)
    assert {i.brand_slug for i in res.items} == {"jollibee"}


@pytest.mark.db
async def test_semantic_ranking_and_top_k(db_session):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(max_distance_km=3.0),
                         query_vec=vec("spaghetti"), k=2)
    assert len(res.items) == 2 and res.candidate_count == 4
    assert res.items[0].food_item == "Jolly Spaghetti"
    assert res.items[0].cosine_distance <= res.items[1].cosine_distance


@pytest.mark.db
async def test_unmatched_branches_never_join(db_session):
    await _fixture_world(db_session)
    res = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG,
                         constraints=SearchConstraints(max_distance_km=0.3), query_vec=vec("coffee"), k=10)
    assert res.items == []  # only Starbucks (unmatched) is within 300 m


@pytest.mark.db
async def test_map_items_toggle_matched_vs_all(db_session):
    await _fixture_world(db_session)
    all_pins = await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=3.0, include_unmatched=True)
    verified = await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=3.0, include_unmatched=False)
    assert [p.poi_name for p in all_pins] == [
        "Starbucks Padre Faura", "Mang Inasal Taft", "Mang Inasal Far", "Jollibee Pedro Gil"
    ]
    assert not all_pins[0].has_calorie_data and all_pins[0].item_count == 0
    assert [p.poi_name for p in verified] == ["Mang Inasal Taft", "Mang Inasal Far", "Jollibee Pedro Gil"]
    taft = verified[0]
    assert taft.item_count == 2 and taft.min_calories == 360 and taft.max_protein_g == 45


# ---------------------------------------------------------------------------
# Full pipeline (discovery disabled -> cache only)
# ---------------------------------------------------------------------------


def make_service() -> HybridRagService:
    async def matcher(_s):
        return BrandMatcher(SEED_SLUGS)

    return HybridRagService(
        discovery=BranchDiscoveryService(None, matcher, enabled=False),
        parser=QueryParser(None),
        embedder=EMB,
        generator=GroundedGenerator(None),
        brands=BrandRegistry(BrandMatcher(SEED_SLUGS)),
    )


@pytest.mark.db
async def test_chat_pipeline_grounded_answer(db_session):
    await _fixture_world(db_session)
    resp = await make_service().chat(db_session, message="grilled chicken under 400 kcal within 3 km",
                                     lat=UPM_LAT, lng=UPM_LNG)
    assert resp.results, resp.answer
    assert all(r.calories <= 400 and r.distance_km <= 3.0 for r in resp.results)
    assert not resp.relaxation.applied
    top = resp.results[0]
    for fact in (top.food_item, top.brand_name, f"{top.calories} kcal", top.poi_name):
        assert fact in resp.answer
    assert resp.inspector.parser_used == "regex"
    assert resp.inspector.discovery[0].source == "disabled"
    assert resp.inspector.generator_used == "template"


@pytest.mark.db
async def test_no_results_do_not_raise_calorie_limit(db_session):
    await _fixture_world(db_session)
    resp = await make_service().chat(db_session, message="anything under 250 kcal", lat=UPM_LAT, lng=UPM_LNG)
    assert not resp.relaxation.applied
    assert resp.relaxation.final_constraints.max_calories == 250
    assert resp.results == []
    assert resp.inspector.retrieval_attempts == 1


@pytest.mark.db
async def test_no_results_do_not_expand_radius(db_session):
    await _fixture_world(db_session)
    resp = await make_service().chat(db_session, message="kenny rogers roast chicken within 3 km", lat=UPM_LAT, lng=UPM_LNG)
    assert not resp.relaxation.applied and resp.results == []
    assert resp.relaxation.final_constraints.max_distance_km == 3.0
    assert len(resp.inspector.discovery) == 0  # absent named food/brand needs no location lookup


@pytest.mark.db
async def test_no_results_even_after_relaxation(db_session):
    await _fixture_world(db_session)
    resp = await make_service().chat(db_session, message="under 100 kcal", lat=UPM_LAT, lng=UPM_LNG)
    assert resp.results == [] and not resp.relaxation.applied
    assert "couldn't find" in resp.answer
    assert resp.inspector.retrieval_attempts == 1


@pytest.mark.db
async def test_sql_injection_payloads_are_inert(db_session):
    await _fixture_world(db_session)
    service = make_service()
    for payload in (
        "'; DROP TABLE nutrition_catalog; --",
        "chicken' OR '1'='1",
        "x UNION SELECT * FROM osm_branch_cache --",
        "1); DELETE FROM nutrition_catalog WHERE (1=1",
    ):
        resp = await service.chat(db_session, message=payload, lat=UPM_LAT, lng=UPM_LNG)
        assert all(r.distance_km <= 5.0 for r in resp.results)
        assert "sql_keywords" in resp.inspector.injection_flags or "'" in payload
    count = (await db_session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one()
    assert count == 6
    tables = (await db_session.execute(text(
        "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
        "('nutrition_catalog', 'osm_branch_cache', 'overpass_fetch_log')"
    ))).scalar_one()
    assert tables == 3


@pytest.mark.db
async def test_chat_endpoint_end_to_end(session_factory, db_session):
    await _fixture_world(db_session)
    app = create_app()
    app.state.rag = make_service()

    async def override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/v1/chat", json={"message": "spaghetti under 500 kcal", "lat": UPM_LAT, "lng": UPM_LNG})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["results"][0]["food_item"] == "Jolly Spaghetti"
        assert r.headers["X-Request-ID"] == body["inspector"]["request_id"]

        r = await client.get("/api/v1/map-items", params={"lat": UPM_LAT, "lng": UPM_LNG, "radius_km": 3,
                                                         "include_unmatched": "false"})
        assert r.status_code == 200
        assert r.json()["matched_count"] == 3 and r.json()["unmatched_count"] == 0

        r = await client.get("/api/v1/brands/Mang Inasal/items")
        assert [i["calories"] for i in r.json()["items"]] == [360, 590]
