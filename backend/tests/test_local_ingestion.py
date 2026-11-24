"""Synthetic fixtures only: no Instagram traffic or private source material."""
import csv
import json
from pathlib import Path

import httpx
import instaloader
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.db.session import get_session, init_db
from app.ingest import (
    DataDirs, PostMeta, import_local, load_reviewed, main, ocr_cache_path, post_images,
    read_csv_rows, read_reviewed, run_ocr, run_parse, scrape, scrape_posts, write_csv_rows,
)
from app.main import create_app
from app.models import NutritionCatalog
from app.schemas import SearchConstraints
from app.services.brand_matcher import BrandRegistry
from app.services.generator import GroundedGenerator
from app.services.google_places import GooglePlacesDiscovery
from app.services.hybrid_rag import HybridRagService, relaxation_ladder
from app.services.ingestion_ocr import (
    UNKNOWN_SERVING, build_context_string, find_tesseract, ocr_image, parse_nutrition_text, tesseract_engine_id,
)
from app.services.query_parser import QueryParser, RegexConstraintParser
from tests.conftest import UPM_LAT, UPM_LNG, add_item
from tests.test_ingest_pipeline import FakePost, _add_post, _review_row, _use_test_db
from tests.test_security import _item


@pytest.fixture
def dirs(tmp_path):
    return DataDirs(tmp_path / "data")


def make_manifest(tmp_path, *, slides=2):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    names = []
    for i in range(slides):
        name = f"slide-{i+1}.png"
        Image.new("RGB", (100, 100), (i, i, i)).save(source / name)
        names.append(name)
    manifest = source / "posts.json"
    manifest.write_text(json.dumps({"posts": [{
        "shortcode": "LOCAL1", "url": "https://www.instagram.com/p/LOCAL1/",
        "caption": "#jollibee", "images": names,
        "source_reference": "Synthetic test fixture; not real nutrition data",
    }]}))
    return manifest


def test_import_carousel_idempotent_and_review_corrections_preserved(dirs, tmp_path):
    manifest = make_manifest(tmp_path)
    result = import_local(dirs, manifest)
    assert result == {"imported_posts": 1, "skipped_known": 0, "imported_images": 2}
    meta = PostMeta.load(dirs.raw / "LOCAL1.meta.json")
    assert meta.images == ["LOCAL1_1.png", "LOCAL1_2.png"]
    assert meta.source_type == "local" and meta.caption == "#jollibee"
    run_ocr(dirs, ocr_fn=lambda p: "Burger 280 kcal", engine_id="fake")
    run_parse(dirs)
    rows = read_csv_rows(dirs.reviewed_csv)
    rows[0].update(calories="290", verified="yes", action="load")
    write_csv_rows(dirs.reviewed_csv, rows)
    before = dirs.reviewed_csv.read_bytes()
    assert import_local(dirs, manifest)["skipped_known"] == 1
    assert run_parse(dirs).appended_rows == 0
    assert dirs.reviewed_csv.read_bytes() == before


def test_import_rejects_changed_media_without_overwriting(dirs, tmp_path):
    manifest = make_manifest(tmp_path)
    import_local(dirs, manifest)
    before = (dirs.raw / "LOCAL1_1.png").read_bytes()
    Image.new("RGB", (100, 100), "red").save(manifest.parent / "slide-1.png")
    with pytest.raises(ValueError, match="conflicts"):
        import_local(dirs, manifest)
    assert (dirs.raw / "LOCAL1_1.png").read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"shortcode": "../escape"}, {"images": ["../outside.png"]}, {"images": []},
    {"images": ["https://example.com/remote.png"]}, {"url": ""}, {"caption": []},
])
def test_import_validates_paths_and_metadata_before_copy(dirs, tmp_path, changes):
    manifest = make_manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["posts"][0].update(changes)
    manifest.write_text(json.dumps(payload))
    with pytest.raises((ValueError, OSError)):
        import_local(dirs, manifest)
    assert not dirs.raw.exists()


def test_import_rejects_duplicate_ids_and_corrupt_image(dirs, tmp_path):
    manifest = make_manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["posts"].append(payload["posts"][0])
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        import_local(dirs, manifest)
    assert not dirs.raw.exists()
    payload["posts"].pop()
    manifest.write_text(json.dumps(payload))
    (manifest.parent / "slide-1.png").write_bytes(b"not a png")
    with pytest.raises(OSError):
        import_local(dirs, manifest)
    assert not dirs.raw.exists()


def test_carousel_sort_is_numeric(dirs):
    dirs.raw.mkdir(parents=True)
    for n in [10, 2, 1]:
        (dirs.raw / f"POST_{n}.jpg").touch()
    (dirs.raw / "POST_OTHER_1.jpg").touch()  # a different shortcode sharing the prefix
    assert post_images(dirs.raw, "POST") == ["POST_1.jpg", "POST_2.jpg", "POST_10.jpg"]


def test_incomplete_download_is_retryable(dirs):
    post = FakePost("PARTIAL")
    post.mediacount = 2

    def download(post):
        Image.new("RGB", (50, 50)).save(dirs.raw / "PARTIAL_1.jpg")

    summary = scrape_posts([post], download, dirs.raw, profile="p", max_new=1, sleeper=lambda _: None)
    assert summary.failed == ["PARTIAL"] and summary.new_posts == 0
    assert not (dirs.raw / "PARTIAL.meta.json").exists()


@pytest.mark.parametrize("error", [
    instaloader.exceptions.AbortDownloadException("401"),
    instaloader.exceptions.TooManyRequestsException("429"),
    instaloader.exceptions.LoginRequiredException("login required"),
])
def test_instagram_listing_refusal_stops_without_retry(dirs, monkeypatch, error):
    calls = []

    def blocked(*args):
        calls.append(1)
        raise error

    monkeypatch.setattr(instaloader.Profile, "from_username", blocked)
    result = scrape(dirs, profile="anongcaloriesnito", max_new=2, login=None, sleep_s=0)
    assert result.stopped_early and result.new_posts == 0 and len(calls) == 1


def test_missing_session_is_recoverable_and_never_enumerates(dirs, monkeypatch):
    def fail(*args):
        raise FileNotFoundError("session file")

    monkeypatch.setattr(instaloader.Instaloader, "load_session_from_file", fail)
    monkeypatch.setattr(instaloader.Profile, "from_username", lambda *a: pytest.fail("must not enumerate"))
    result = scrape(dirs, profile="p", max_new=1, login="my-account", sleep_s=0)
    assert result.stopped_early.startswith("session_error")


def test_wrong_session_identity_stops_before_listing(dirs, monkeypatch):
    monkeypatch.setattr(instaloader.Instaloader, "load_session_from_file", lambda *a: None)
    monkeypatch.setattr(instaloader.Instaloader, "test_login", lambda *a: "another-account")
    monkeypatch.setattr(instaloader.Profile, "from_username", lambda *a: pytest.fail("must not enumerate"))
    assert "authentication_failed" in scrape(dirs, profile="p", max_new=1, login="mine", sleep_s=0).stopped_early


def test_partial_ocr_is_not_frozen_in_review_and_cache_corruption_recovers(dirs):
    _add_post(dirs, "SC1", "#jollibee", None)
    summary = run_parse(dirs)
    assert summary.posts_missing_ocr == ["SC1"] and not dirs.reviewed_csv.exists()
    dirs.ocr.mkdir(parents=True)
    ocr_cache_path(dirs.ocr, "SC1_1.jpg").write_text("{truncated")
    assert run_ocr(dirs, ocr_fn=lambda _: "Burger 280 kcal", engine_id="fake").processed == 1
    assert run_parse(dirs).appended_rows == 1
    assert read_csv_rows(dirs.reviewed_csv)[0]["action"] == "skip"


def test_parser_rejects_stale_ocr_after_source_image_changes(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Burger 280 kcal"])
    Image.new("RGB", (90, 90), "red").save(dirs.raw / "SC1_1.jpg")
    result = run_parse(dirs)
    assert result.items == 0 and result.posts_missing_ocr == ["SC1"]
    assert not dirs.reviewed_csv.exists()


def test_failed_ocr_refresh_cannot_leave_old_cache_available(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Burger 280 kcal"])

    def broken(_):
        raise RuntimeError("engine failed")

    assert run_ocr(dirs, ocr_fn=broken, engine_id="new-engine").failed == ["SC1_1.jpg"]
    assert run_parse(dirs).posts_missing_ocr == ["SC1"]


def test_changed_preprocessing_version_requires_new_ocr(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Burger 280 kcal"])
    cache = ocr_cache_path(dirs.ocr, "SC1_1.jpg")
    payload = json.loads(cache.read_text())
    payload["engine"] = "tesseract-5|eng|--psm 6|pre0"
    cache.write_text(json.dumps(payload))
    assert run_parse(dirs).posts_missing_ocr == ["SC1"]


def test_context_preserves_decimal_macro_precision():
    assert "fat 11.55 g" in build_context_string("Fixture Kitchen", "Meal", "100g", 400, fat_g=11.55)


def test_carousel_state_never_leaks_between_slides(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Burger 280 kcal", "Protein 90g\nSpaghetti 480 kcal"])
    run_parse(dirs)
    rows = read_csv_rows(dirs.reviewed_csv)
    assert [r["protein_g"] for r in rows] == ["", ""]


def test_caption_and_slides_with_conflicting_values_require_review(dirs):
    _add_post(dirs, "SC1", "#jollibee\nBurger 290 kcal", ["Burger 280 kcal", "Burger 300 kcal"])
    run_parse(dirs)
    row, = read_csv_rows(dirs.reviewed_csv)
    assert row["is_suspect"] == "yes" and row["action"] == "skip" and row["verified"] == "no"
    assert "conflicting_source_values" in row["warnings"]


def test_legacy_csv_header_is_preserved_when_appending(dirs):
    legacy = {k: v for k, v in _review_row(shortcode="OLD").items() if not k.startswith("price_")}
    dirs.review.mkdir(parents=True)
    with dirs.reviewed_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(legacy))
        writer.writeheader()
        writer.writerow(legacy)
    before = dirs.reviewed_csv.read_bytes()
    _add_post(dirs, "NEW", "#jollibee", ["Spaghetti 480 kcal"])
    run_parse(dirs)
    assert dirs.reviewed_csv.read_bytes().startswith(before)
    assert read_reviewed(dirs.reviewed_csv).errors == []


@pytest.mark.parametrize("text,calories,serving", [
    ("Burger 280 kcal", 280, UNKNOWN_SERVING),
    ("Meal 1,200 kcal", 1200, UNKNOWN_SERVING),
    ("Water 0 kcal", 0, UNKNOWN_SERVING),
    ("Burger\nServing size: 100 g\n280 kcal", 280, "100 g"),
    ("Burger (100 g) 280 kcal", 280, "100 g"),
])
def test_parser_preserves_missing_and_explicit_nutrition(matcher, text, calories, serving):
    item, = parse_nutrition_text("#jollibee", text, matcher)
    assert (item.calories, item.serving_size, item.protein_g, item.price_php) == (calories, serving, None, None)


def test_ambiguous_calories_flagged_not_verified(matcher):
    item, = parse_nutrition_text("#jollibee", "Burger 280 kcal per 100g or 560 kcal per serving", matcher)
    assert item.is_suspect and "ambiguous_calories" in item.warnings


@pytest.mark.parametrize("price", ["PHP120.50", "₱120.50"])
def test_visible_php_prices_are_only_review_proposals(matcher, price):
    item, = parse_nutrition_text("#jollibee", f"Fixture Bowl {price} 400 kcal", matcher,
                               source_post_url="https://example.org/authorized-fixture")
    assert item.price_php == 120.5 and item.price_source_url == "https://example.org/authorized-fixture"
    assert "price_requires_human_review" in item.warnings


def test_ambiguous_inline_prices_are_suspect(matcher):
    item, = parse_nutrition_text("#jollibee", "Fixture Bowl PHP100 or PHP120 400 kcal", matcher)
    assert item.is_suspect and "ambiguous_price" in item.warnings


def test_carousel_slide_provenance_and_prices_remain_reviewable(dirs):
    _add_post(dirs, "PROVENANCE", "#jollibee", ["Fixture Bowl PHP120 400 kcal", "Fixture Soup 200 kcal"])
    run_parse(dirs)
    rows = read_csv_rows(dirs.reviewed_csv)
    assert rows[0]["source_images"] == "PROVENANCE_1.jpg" and rows[0]["price_php"] == "120"
    assert rows[1]["source_images"] == "PROVENANCE_2.jpg" and rows[1]["price_php"] == ""
    assert all(row["action"] == "skip" and row["verified"] == "no" for row in rows)
    rows[0]["action"] = "load"
    write_csv_rows(dirs.reviewed_csv, rows)
    assert "human verification" in " ".join(read_reviewed(dirs.reviewed_csv).errors)


@pytest.mark.db
async def test_reviewed_source_update_preserves_human_authority(dirs, db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    original = _review_row(verified="yes", calories="400", source_images="FIRST_1.png")
    write_csv_rows(dirs.reviewed_csv, [original])
    await load_reviewed(dirs.reviewed_csv)
    write_csv_rows(dirs.reviewed_csv, [{**original, "verified": "no", "calories": "999", "source_images": "NEW_1.png"}])
    await load_reviewed(dirs.reviewed_csv)
    row = (await db_session.execute(select(NutritionCatalog))).scalar_one()
    assert row.calories == 400 and row.source_images == ["FIRST_1.png"]
    await db_session.commit()  # release the reader transaction before load's schema initialization
    write_csv_rows(dirs.reviewed_csv, [{**original, "calories": "410", "source_images": "RECHECKED_1.png"}])
    await load_reviewed(dirs.reviewed_csv)
    await db_session.refresh(row)
    assert row.calories == 410 and row.source_images == ["RECHECKED_1.png"] and row.is_verified


def test_fractional_calories_are_flagged_for_review(matcher):
    item, = parse_nutrition_text("#jollibee", "Burger 280.5 kcal", matcher)
    assert item.is_suspect and "fractional_calories_require_review" in item.warnings


@pytest.mark.parametrize("overrides,expected", [
    ({"price_php": "100"}, "price_source_url"),
    ({"price_php": "100", "price_source_url": "https://example.org/menu"}, "human verification"),
    ({"price_php": "100", "verified": "yes", "price_source_url": "javascript:alert(1)"}, "HTTP(S)"),
    ({"price_php": "100.123", "verified": "yes", "price_source_url": "https://example.org/menu"}, "decimal places"),
    ({"price_php": "NaN"}, "outside"),
    ({"protein_g": "Infinity"}, "outside"),
    ({"protein_g": "12.345"}, "decimal places"),
    ({"protein_g": "12,5"}, "ambiguous comma"),
    ({"protein_g": "90"}, "suspect nutrition"),
    ({"is_suspect": "yes"}, "suspect nutrition"),
])
def test_review_validation_blocks_unsupported_prices_and_suspect_values(dirs, overrides, expected):
    write_csv_rows(dirs.reviewed_csv, [_review_row(**overrides)])
    assert expected in "\n".join(read_reviewed(dirs.reviewed_csv).errors)


def test_missing_calories_never_become_zero(dirs):
    write_csv_rows(dirs.reviewed_csv, [_review_row(calories="")])
    reviewed = read_reviewed(dirs.reviewed_csv)
    assert "calories is required" in reviewed.errors[0] and reviewed.items == []


@pytest.mark.parametrize("query", ["under PHP150", "under ₱150", "under 150 pesos", "budget 150"])
def test_budget_extraction_does_not_become_calorie_limit(matcher, query):
    constraints, _, _ = RegexConstraintParser().parse(query + " chicken under 500 kcal", matcher, 2)
    assert constraints.max_price_php == 150 and constraints.max_calories == 500
    constraints, _, _ = RegexConstraintParser().parse(query + " chicken", matcher, 2)
    assert constraints.max_price_php == 150 and constraints.max_calories is None
    assert all(c.max_price_php == 150 for _, c in relaxation_ladder(constraints))


class ChoiceLLM:
    def __init__(self, payload):
        self.payload = payload

    async def generate_json(self, system, prompt, schema):
        return self.payload


@pytest.mark.parametrize("payload", [
    {"recommended_item_id": "invented"},
    {"recommended_item_id": "1", "answer": "99g protein and 50 pesos"},
    {"answer": "one hundred calories, free, two servings"},
    {"recommended_item_id": 1},
])
async def test_llm_cannot_add_unsupported_numbers_or_extra_facts(payload):
    answer, used = await GroundedGenerator(ChoiceLLM(payload)).generate("burger", [_item(280)], SearchConstraints())
    assert used == "template" and "280 kcal" in answer
    assert "99g" not in answer and "50 pesos" not in answer and "free" not in answer


async def test_llm_selects_existing_record_but_facts_come_from_storage():
    item = _item(280)
    answer, used = await GroundedGenerator(ChoiceLLM({"recommended_item_id": "1"})).generate(
        "burger", [item], SearchConstraints())
    assert used == "llm" and "Suggested match: Yumburger" in answer and "protein 12 g" in answer
    assert "price unavailable" in answer


@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract binary not installed")
@pytest.mark.parametrize("dark", [False, True])
def test_small_table_text_with_real_tesseract(tmp_path, matcher, dark):
    image = Image.new("RGB", (650, 220), "black" if dark else "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=24)
    for n, value in enumerate(["Food               Calories       Protein",
                               "Fixture Bowl      400 kcal      20g protein",
                               "Fixture Soup      200 kcal      10g protein"]):
        draw.text((12, 18 + n * 60), value, fill="white" if dark else "black", font=font)
    path = tmp_path / "table.png"
    image.save(path)
    items = parse_nutrition_text("#jollibee", ocr_image(path, psm=6), matcher)
    # Tesseract may merge a word boundary in small text. Preserve its output for review;
    # this check verifies the calorie/macro columns and row associations, not guessed spelling.
    assert [(i.food_item.replace(" ", ""), i.calories, i.protein_g) for i in items] == [
        ("FixtureBowl", 400, 20), ("FixtureSoup", 200, 10),
    ]
    assert tesseract_engine_id("eng", 6) != tesseract_engine_id("eng", 11)


@pytest.mark.db
async def test_load_rolls_back_if_branch_rematching_fails(dirs, db_session, monkeypatch, pg_url):
    from app.db import maintenance
    _use_test_db(monkeypatch, pg_url)
    write_csv_rows(dirs.reviewed_csv, [_review_row()])

    async def fail(*args, **kwargs):
        raise RuntimeError("rematch interrupted")

    monkeypatch.setattr(maintenance, "rematch_branches", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        await load_reviewed(dirs.reviewed_csv)
    assert (await db_session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one() == 0


@pytest.mark.db
async def test_additive_schema_upgrade_preserves_old_catalog(db_engine, db_session):
    # Emulate an existing schema before price fields were introduced.
    await add_item(db_session, brand_name="Fixture Kitchen", brand_slug="fixture kitchen",
                   food_item="Existing Item", calories=400)
    await db_session.execute(text("ALTER TABLE nutrition_catalog DROP COLUMN price_php CASCADE"))
    await db_session.execute(text("ALTER TABLE nutrition_catalog DROP COLUMN price_source_url"))
    await db_session.commit()
    await init_db(db_engine)
    await init_db(db_engine)
    columns = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='nutrition_catalog'"
    ))).scalars().all()
    assert {"price_php", "price_source_url", "embedding_model"} <= set(columns)
    row = (await db_session.execute(select(NutritionCatalog))).scalar_one()
    assert row.food_item == "Existing Item" and row.calories == 400 and row.price_php is None


@pytest.mark.db
@pytest.mark.skipif(find_tesseract() is None, reason="Tesseract binary not installed")
async def test_real_local_carousel_through_ocr_review_database_places_and_api(
    dirs, tmp_path, db_session, session_factory, monkeypatch, pg_url, embedder,
):
    """Real file import, Tesseract, Postgres/pgvector and ASGI API; only Google is mocked."""
    _use_test_db(monkeypatch, pg_url)
    manifest = make_manifest(tmp_path, slides=3)
    # These are deliberately synthetic values, not claims about a real restaurant.
    for n, content in enumerate([
        ["Fixture Rice Bowl", "400 kcal", "Protein 20g Carbs 50g Fat 13g", "Serving size: 1 bowl"],
        ["Fixture Noodles", "450 kcal", "Serving size: 1 bowl"],
        ["Fixture Platter", "700 kcal", "Serving size: 1 plate"],
    ], start=1):
        image = Image.new("RGB", (1500, 600), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=48)
        for line, value in enumerate(content):
            draw.text((40, 35 + line * 120), value, font=font, fill="black")
        image.save(manifest.parent / f"slide-{n}.png")
    payload = json.loads(manifest.read_text())
    payload["posts"][0]["caption"] = "Brand: CalCipher Fixture Kitchen"
    manifest.write_text(json.dumps(payload))
    assert import_local(dirs, manifest)["imported_images"] == 3
    result = run_ocr(dirs, ocr_fn=ocr_image, engine_id=tesseract_engine_id("eng"))
    assert result.processed == 3 and result.failed == []
    assert run_ocr(dirs, ocr_fn=ocr_image, engine_id=tesseract_engine_id("eng")).cached == 3
    assert run_parse(dirs).items == 3
    rows = read_csv_rows(dirs.reviewed_csv)
    assert all(row["verified"] == "no" and row["action"] == "skip" for row in rows)
    for row in rows:
        row.update(action="load", verified="yes")
        if row["food_item"] == "Fixture Rice Bowl":
            row.update(price_php="120.00", price_source_url="https://example.org/synthetic-menu")
        if row["food_item"] == "Fixture Platter":
            row.update(price_php="90.00", price_source_url="https://example.org/synthetic-menu")
    write_csv_rows(dirs.reviewed_csv, rows)
    assert (await load_reviewed(dirs.reviewed_csv, dry_run=True))["to_load"] == 3
    assert (await load_reviewed(dirs.reviewed_csv))["written"] == 3
    await load_reviewed(dirs.reviewed_csv)
    stored = (await db_session.execute(select(NutritionCatalog))).scalars().all()
    assert len(stored) == 3 and all(len(row.embedding) == 768 and row.embedding_model == "hash-768" for row in stored)
    assert "price PHP 120.00" in next(row.context_string for row in stored if row.food_item == "Fixture Rice Bowl")

    brands = BrandRegistry()
    await brands.refresh(db_session)

    def google(request):
        return httpx.Response(200, json={"places": [{
            "id": "synthetic-branch", "displayName": {"text": "CalCipher Fixture Kitchen"},
            "primaryType": "restaurant", "userRatingCount": 10,
            "location": {"latitude": UPM_LAT, "longitude": UPM_LNG},
        }]})

    discovery = GooglePlacesDiscovery(SecretStr("test"), brands.refresh_if_stale,
                                      transport=httpx.MockTransport(google))
    app = create_app()
    app.state.rag = HybridRagService(discovery=discovery, parser=QueryParser(), embedder=embedder,
                                    generator=GroundedGenerator(), brands=brands)

    async def session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={
            "message": "rice under 500 kcal under PHP150 within 2 km", "lat": UPM_LAT, "lng": UPM_LNG,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert [row["food_item"] for row in body["results"]] == ["Fixture Rice Bowl"]
        assert body["results"][0]["price_php"] == 120 and body["results"][0]["is_verified"]
        assert body["inspector"]["constraints"]["max_price_php"] == 150
        assert "400 kcal" in body["answer"] and "PHP 120.00" in body["answer"]
        response = await client.get("/api/v1/brands/calcipher fixture kitchen/items")
        assert response.status_code == 200 and len(response.json()["items"]) == 3
        response = await client.get("/api/v1/map-items", params={"lat": UPM_LAT, "lng": UPM_LNG})
        assert response.status_code == 200 and response.json()["matched_count"] == 1
        response = await client.post("/api/v1/chat", json={
            "message": "under PHP50", "lat": UPM_LAT, "lng": UPM_LNG,
        })
        assert response.json()["results"] == []
        assert "without a verified price" in response.json()["answer"]
