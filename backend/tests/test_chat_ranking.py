"""Chat uses Search's catalog-wide retrieval and ranks by stored nutrients with SQL, not similarity."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models import NutritionCatalog
from app.schemas import SearchConstraints
from app.security import MacroPhysicsError
from app.services.brand_matcher import BrandMatcher, BrandRegistry
from app.services.brand_matcher import load_brand_matcher
from app.services.generator import GroundedGenerator
from app.services.hybrid_rag import search_catalog
from app.services.query_parser import RegexConstraintParser
from tests.conftest import UPM_LAT, UPM_LNG, add_item
from tests.test_hybrid_rag import make_service

MENU = [
    # brand, slug, item, kcal, protein, carbs, fat
    ("KFC", "kfc", "Zinger Chicken Sandwich", 520, 32, 40, 25),
    ("KFC", "kfc", "Chicken Hot Shots", 300, 18, 20, 15),
    ("KFC", "kfc", "Famous Bowl", 700, 26, 80, 30),
    ("KFC", "kfc", "Coleslaw", 150, None, None, None),
    ("Jollibee", "jollibee", "2-pc Chickenjoy", 580, 40, 10, 36),
    ("Jollibee", "jollibee", "Jolly Spaghetti", 480, 15, 70, 15.5),
    ("Jollibee", "jollibee", "Peach Mango Pie", 230, None, None, None),
    ("McDonald's", "mcdonalds", "McChicken", 400, 14, 40, 20),
    ("McDonald's", "mcdonalds", "Big Mac", 550, 25, 46, 30),
    ("Mang Inasal", "mang inasal", "Chicken Inasal Pecho", 360, 45, None, 18.5),
]


async def _menu(session):
    for brand, slug, item, kcal, protein, carbs, fat in MENU:
        await add_item(session, brand_name=brand, brand_slug=slug, food_item=item, calories=kcal,
                       protein_g=protein, carbs_g=carbs, fat_g=fat, verified=False)
    service = make_service()
    service.brands = BrandRegistry(await load_brand_matcher(session, include_pending=True))
    # Chat must answer from the catalog without looking up branches unless asked about the pin.
    service.discovery.ensure_branches = AsyncMock(side_effect=AssertionError("no branch lookup expected"))
    return service


async def ask(service, session, message):
    return await service.chat(session, message=message, lat=UPM_LAT, lng=UPM_LNG)


@pytest.mark.db
async def test_chat_finds_a_food_across_restaurants_exactly_like_search(db_session):
    service = await _menu(db_session)
    reply = await ask(service, db_session, "chicken")
    names = {item.food_item for item in reply.menu_matches}
    assert names == {"Zinger Chicken Sandwich", "Chicken Hot Shots", "2-pc Chickenjoy", "McChicken", "Chicken Inasal Pecho"}
    assert {item.brand_name for item in reply.menu_matches} == {"KFC", "Jollibee", "McDonald's", "Mang Inasal"}
    search = await search_catalog(db_session, RegexConstraintParser().parse("chicken", service.brands.matcher, 3.0)[0])
    assert names == {item.food_item for item in search}  # Search and Chat share one retrieval
    assert reply.menu_match_count == 5 and reply.ranking is None and reply.inspector.discovery == []
    assert "5 menu items matching “chicken” from 4 restaurants" in reply.answer


@pytest.mark.db
async def test_high_protein_ranks_every_restaurant_by_sql_and_skips_missing_values(db_session):
    service = await _menu(db_session)
    reply = await ask(service, db_session, "high protein items")
    proteins = [item.protein_g for item in reply.menu_matches]
    assert proteins == [45, 40, 32, 26, 25]  # every item with >= 20 g, highest first, no nulls
    assert reply.ranking.field == "protein_g" and reply.ranking.direction == "desc"
    assert reply.answer.splitlines()[1].startswith("1. Chicken Inasal Pecho — Mang Inasal: 45 g protein · 360 kcal")
    assert "carbs not listed" in reply.answer.splitlines()[1]  # a missing macro is named, not guessed


@pytest.mark.db
async def test_restaurant_superlative_returns_the_true_top_item(db_session):
    service = await _menu(db_session)
    reply = await ask(service, db_session, "highest protein item in kfc")
    assert [item.food_item for item in reply.menu_matches] == ["Zinger Chicken Sandwich", "Famous Bowl", "Chicken Hot Shots"]
    assert reply.answer.startswith("Highest protein at KFC:\n1. Zinger Chicken Sandwich — KFC: 32 g protein")
    assert "1 matching item doesn’t list protein, so it isn’t ranked." in reply.answer  # Coleslaw
    top = (await db_session.execute(select(func.max(NutritionCatalog.protein_g))
                                    .where(NutritionCatalog.brand_slug == "kfc"))).scalar_one()
    assert reply.menu_matches[0].protein_g == float(top)


@pytest.mark.db
@pytest.mark.parametrize("message,expected", [
    ("lowest calorie item in Jollibee", ["Peach Mango Pie", "Jolly Spaghetti", "2-pc Chickenjoy"]),
    ("Which McDonald's item has the most fat?", ["Big Mac", "McChicken"]),
    ("high protein under 400 calories", ["Chicken Inasal Pecho"]),
    ("low carb chicken under 500 calories", ["Chicken Hot Shots", "McChicken"]),
    ("most fat at mc donalds", ["Big Mac", "McChicken"]),
    ("chicken with at least 30 g protein and under 30g carbs", ["2-pc Chickenjoy"]),
])
async def test_rankings_and_limits_work_for_any_brand_and_nutrient(db_session, message, expected):
    service = await _menu(db_session)
    reply = await ask(service, db_session, message)
    assert [item.food_item for item in reply.menu_matches] == expected, reply.answer


@pytest.mark.db
async def test_low_carb_reports_items_without_carbs_instead_of_treating_them_as_zero(db_session):
    service = await _menu(db_session)
    reply = await ask(service, db_session, "low carb chicken under 500 calories")
    assert all(item.carbs_g is not None for item in reply.menu_matches)
    assert "1 matching item doesn’t list carbs" in reply.answer  # Chicken Inasal Pecho


@pytest.mark.db
async def test_edge_cases_say_what_is_missing_instead_of_inventing(db_session):
    service = await _menu(db_session)
    no_carbs = await ask(service, db_session, "highest carbs at Mang Inasal")
    assert no_carbs.menu_matches == []
    assert no_carbs.answer == "1 menu item at Mang Inasal matches, but none list carbs, so I can’t rank them by carbs."
    unknown = await ask(service, db_session, "highest protein item at Starbucks")
    assert unknown.menu_matches == [] and "“Starbucks”" in unknown.answer and "CalCipher catalog" in unknown.answer
    empty = await ask(service, db_session, "chicken under 100 calories")
    assert empty.menu_matches == []
    assert "No menu items matching “chicken” with at most 100 kcal" in empty.answer and "loosening" in empty.answer
    fiber = await ask(service, db_session, "highest fiber breakfast in Jollibee")
    assert fiber.menu_matches == [] and "doesn’t record fiber" in fiber.answer
    assert "calories, protein, carbs and fat" in fiber.answer
    with pytest.raises(MacroPhysicsError):
        await ask(service, db_session, "at least 50g carbs and under 20g carbs")


@pytest.mark.db
async def test_ties_and_descriptor_fallback_are_explained(db_session):
    service = await _menu(db_session)
    await add_item(db_session, brand_name="Jollibee", brand_slug="jollibee", food_item="Chicken Burger Steak",
                   calories=410, protein_g=45, carbs_g=30, fat_g=20, verified=False)
    tie = await ask(service, db_session, "most protein")
    assert "2 items tie at 45 g." in tie.answer
    grilled = await ask(service, db_session, "grilled chicken")
    assert grilled.menu_matches and grilled.answer.startswith("No menu names mention “grilled”")


@pytest.mark.db
async def test_injection_text_in_ranked_questions_is_inert(db_session):
    service = await _menu(db_session)
    for payload in ["highest protein'; DROP TABLE nutrition_catalog; --", "lowest fat) OR 1=1 --", "most protein at kfc' UNION SELECT 1"]:
        await ask(service, db_session, payload)
    count = (await db_session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one()
    assert count == len(MENU)


class _PickingLLM:
    def __init__(self, answer):
        self.answer = answer

    async def generate_json(self, system, prompt, schema):
        assert "null nutrient" in system and "<retrieved_records>" in prompt
        return {"recommended_item_id": self.answer(prompt)}


@pytest.mark.db
async def test_llm_can_only_point_at_a_retrieved_item(db_session):
    service = await _menu(db_session)
    service.generator = GroundedGenerator(_PickingLLM(lambda prompt: "not-a-real-id"))
    invented = await ask(service, db_session, "chicken")
    assert "Suggested match" not in invented.answer and invented.inspector.generator_used == "template"
    service.generator = GroundedGenerator(_PickingLLM(lambda prompt: None))
    assert "Suggested match" not in (await ask(service, db_session, "chicken")).answer
    picked = {}

    def choose(prompt):
        import json
        records = json.loads(prompt.split("<retrieved_records>\n")[1].split("\n</retrieved_records>")[0])
        picked.update(records[0])
        return records[0]["item_id"]

    service.generator = GroundedGenerator(_PickingLLM(choose))
    chosen = await ask(service, db_session, "chicken")
    assert chosen.answer.startswith(f"Suggested match: {picked['food_item']} — {picked['brand_name']}.")
    ranked = await ask(service, db_session, "highest protein item in kfc")
    assert "Suggested match" not in ranked.answer  # rankings are SQL-ordered; the model is not consulted


def test_parser_reads_rankings_limits_and_spacing():
    matcher = BrandMatcher(["kfc", "jollibee", "mcdonalds", "mang inasal"])
    parse = lambda text: RegexConstraintParser().parse(text, matcher, 3.0)[0]
    assert (parse("highest protein item in KFC").sort_by, parse("highest protein item in KFC").target_brand) == ("protein_g", "kfc")
    assert parse("low carb chicken").sort_direction == "asc" and parse("low carb chicken").semantic_query == "chicken"
    assert parse("no more than 800 calories").max_calories == 800
    assert parse("over 600 calories").min_calories == 600 and parse("over 600 calories").max_calories is None
    assert parse("under 20g protein").max_protein_g == 20 and parse("under 20g protein").min_protein_g is None
    assert parse("fat below 10 g burger").max_fat_g == 10
    assert parse("mc donalds burgers").target_brand == "mcdonalds"
    assert isinstance(parse("chicken"), SearchConstraints) and parse("chicken").sort_by is None
