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
