from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.models import NutritionCatalog
from app.services.food_matching import food_pattern, question_field, search_order
from tests.conftest import UPM_LAT, UPM_LNG, add_branch, add_item
from tests.test_hybrid_rag import make_service


@pytest.mark.parametrize("query", ["heavy snack", "What can I eat for a heavy snack?", "recommend a filling snack please"])
def test_filling_snack_is_a_food_category_not_a_literal_heavy_ingredient(query):
    pattern = food_pattern(query)
    assert pattern and "sandwich" in pattern and "heavy" not in pattern
    assert search_order(query) == "calories_desc"


@pytest.mark.parametrize("query,field", [
    ("How many calories are in Buchi?", "calories"),
    ("How much protein does Buchi contain?", "protein_g"),
    ("What are the macros in Buchi?", "macros"),
    ("How much is Buchi?", "price"),
    ("What can I eat under 500 calories?", None),
    ("heavy snack", None),
])
def test_questions_are_distinct_from_meal_searches(query, field):
    assert question_field(query) == field


@pytest.mark.db
async def test_heavy_and_light_snacks_rank_real_snacks_and_keep_explicit_limits(db_session):
    for name, calories in [("Cheese burger", 430), ("Siopao", 260), ("Americano", 5), ("Chicken curry rice", 800),
                           ("Burger steak with rice", 500), ("Chicken burger with drink", 731), ("Buns (3 pieces)", 1239)]:
        await add_item(db_session, brand_name="Synthetic Cafe", brand_slug="synthetic cafe", food_item=name, calories=calories, verified=False)
    await add_branch(db_session, osm_id=899881, poi_name="Synthetic Cafe", lat=UPM_LAT, lng=UPM_LNG, slug="synthetic cafe")
    service = make_service()
    heavy = await service.chat(db_session,message="heavy snack",lat=UPM_LAT,lng=UPM_LNG)
    assert [x.calories for x in heavy.catalog_matches] == [430, 260, 1239]
    assert "430 kcal" in heavy.answer and "pending review" in heavy.answer
    assert not heavy.results
    light = await service.chat(db_session,message="light snack",lat=UPM_LAT,lng=UPM_LNG)
    assert [x.calories for x in light.catalog_matches] == [260, 430, 1239]
    limited = await service.chat(db_session,message="heavy snack under 300 kcal",lat=UPM_LAT,lng=UPM_LNG)
    assert [x.food_item for x in limited.catalog_matches] == ["Siopao"]


@pytest.mark.db
async def test_fact_questions_use_catalog_without_inventing_a_nearby_branch_or_missing_values(db_session):
    await add_item(db_session, brand_name="Jollibee", brand_slug="jollibee", food_item="Buchi", calories=125, verified=False)
    service = make_service()
    service.discovery.ensure_branches = AsyncMock(side_effect=AssertionError("A menu fact needs no branch discovery"))
    calories = await service.chat(db_session,message="How many calories are in Jollibee Buchi?",lat=UPM_LAT,lng=UPM_LNG)
    assert "125 kcal" in calories.answer and "pending review" in calories.answer
    assert len(calories.catalog_answers) == 1 and not calories.results and not calories.catalog_matches
    assert calories.inspector.discovery == []
    protein = await service.chat(db_session,message="How much protein does Jollibee Buchi contain?",lat=UPM_LAT,lng=UPM_LNG)
    assert "Protein is not listed" in protein.answer
    price = await service.chat(db_session,message="How much is Jollibee Buchi?",lat=UPM_LAT,lng=UPM_LNG)
    assert "No checked price" in price.answer and "PHP 0" not in price.answer
    await db_session.execute(update(NutritionCatalog).values(is_active=False))
    archived = await service.chat(db_session,message="How many calories are in Buchi?",lat=UPM_LAT,lng=UPM_LNG)
    assert not archived.catalog_answers and "don’t have a matching item" in archived.answer


@pytest.mark.db
async def test_unspecified_nutrition_question_and_greeting_get_useful_replies(db_session):
    service = make_service()
    service.discovery.ensure_branches = AsyncMock(side_effect=AssertionError("No lookup needed"))
    reply = await service.chat(db_session,message="How many calories?",lat=UPM_LAT,lng=UPM_LNG)
    assert "Which dish or restaurant" in reply.answer
    hello = await service.chat(db_session,message="hello",lat=UPM_LAT,lng=UPM_LNG)
    assert "heavy snack" in hello.answer and not hello.results


@pytest.mark.db
async def test_followups_keep_the_food_and_limits_but_a_new_food_starts_fresh(db_session):
    for name, calories in [("Cheese burger", 430), ("Siopao", 260)]:
        await add_item(db_session,brand_name="Synthetic Cafe",brand_slug="synthetic cafe",food_item=name,calories=calories,verified=False)
    await add_branch(db_session,osm_id=899882,poi_name="Synthetic Cafe",lat=UPM_LAT,lng=UPM_LNG,slug="synthetic cafe")
    service = make_service()
    previous = "heavy snack under 450 kcal within 2 km"
    lighter = await service.chat(db_session,message="something lighter",previous_message=previous,lat=UPM_LAT,lng=UPM_LNG,default_radius_km=1)
    assert [x.calories for x in lighter.catalog_matches] == [260,430]
    assert lighter.inspector.constraints.max_calories == 450
    assert lighter.inspector.constraints.max_distance_km == 2
    lower_limit = await service.chat(db_session,message="what about under 300 kcal",previous_message=previous,lat=UPM_LAT,lng=UPM_LNG)
    assert [x.calories for x in lower_limit.catalog_matches] == [260]
    protein = await service.chat(db_session,message="what about protein",previous_message="Siopao",lat=UPM_LAT,lng=UPM_LNG)
    assert len(protein.catalog_answers)==1 and "not listed" in protein.answer
    banana = await service.chat(db_session,message="banana",previous_message=previous,lat=UPM_LAT,lng=UPM_LNG)
    assert not banana.results and not banana.catalog_matches and not banana.catalog_answers
    assert banana.inspector.constraints.max_calories is None
