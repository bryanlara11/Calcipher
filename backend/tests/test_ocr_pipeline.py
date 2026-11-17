import shutil

import pytest
from PIL import Image, ImageDraw

from app.services import ingestion_ocr
from app.services.ingestion_ocr import (
    DownloadedPost,
    build_context_string,
    fix_ocr_digits,
    macro_energy_consistent,
    parse_nutrition_text,
    preprocess_image,
    process_post,
    upsert_catalog_items,
)

POST_URL = "https://www.instagram.com/p/TEST123/"


def test_single_item_caption_style(matcher):
    caption = "Jollibee Chickenjoy with rice 🍗 #jollibee #anongcaloriesnito"
    ocr = """CHICKENJOY 1PC WITH RICE
    540 kcal
    Protein: 26g  Carbs: 50g  Fat: 26g
    Serving size: 1 pc chicken + 1 cup rice"""
    items = parse_nutrition_text(caption, ocr, matcher, source_post_url=POST_URL)
    assert len(items) == 1
    item = items[0]
    assert (item.brand_slug, item.brand_name) == ("jollibee", "Jollibee")
    assert item.food_item == "CHICKENJOY 1PC WITH RICE"
    assert (item.calories, item.protein_g, item.carbs_g, item.fat_g) == (540, 26.0, 50.0, 26.0)
    assert item.serving_size == "1 pc chicken + 1 cup rice"
    assert item.source_post_url == POST_URL
    assert not item.is_suspect


def test_multi_item_post_with_inline_values(matcher):
    ocr = """ANDOK'S MENU
    Dokito Burger - 410 calories | P 16g C 42g F 20g
    Litson Manok Quarter (Pecho) 390 cal
    protein 42g carbs 2g fat 24g
    Liempo with Rice 820 kcal"""
    items = parse_nutrition_text("Andok's edition 🔥", ocr, matcher)
    assert [(i.food_item, i.calories) for i in items] == [
        ("Dokito Burger", 410),
        ("Litson Manok Quarter (Pecho)", 390),
        ("Liempo with Rice", 820),
    ]
    assert items[0].protein_g == 16 and items[0].fat_g == 20
    assert items[1].protein_g == 42 and items[1].carbs_g == 2
    assert items[2].protein_g is None  # no macros given -> stays null, not invented
    assert all(i.brand_slug == "andoks" for i in items)


def test_item_name_on_previous_line(matcher):
    ocr = "Mang Inasal\nChicken Inasal Paa with Rice\nCalories: 620\nProtein 34g"
    items = parse_nutrition_text("", ocr, matcher)
    assert [(i.food_item, i.calories, i.protein_g) for i in items] == [("Chicken Inasal Paa with Rice", 620, 34.0)]


def test_ocr_digit_confusions_are_repaired(matcher):
    assert fix_ocr_digits("5O0 kcal") == "500 kcal"
    assert fix_ocr_digits("Protein l2g") == "Protein 12g"
    assert fix_ocr_digits("Solo meal") == "Solo meal"  # words without digits untouched
    items = parse_nutrition_text("#subway", "Roasted Chicken Sub 32O kcal", matcher)
    assert items[0].calories == 320


def test_unknown_brand_from_explicit_brand_line(matcher):
    items = parse_nutrition_text("📍 Brand: Army Navy Burger + Burrito", "Classic Burger 650 kcal", matcher)
    assert items[0].brand_slug == "army navy burger burrito"
    assert items[0].brand_name == "Army Navy Burger + Burrito"


def test_post_without_brand_is_skipped(matcher):
    assert parse_nutrition_text("yummy!", "Mystery Meal 500 kcal", matcher) == []


@pytest.mark.parametrize("bad", ["Huge Platter 25000 kcal", "Water 0 kcal"])
def test_out_of_range_calories_dropped(matcher, bad):
    assert parse_nutrition_text("#jollibee", bad, matcher) == []


def test_macro_mismatch_flagged_as_suspect(matcher):
    # 4*60 + 4*80 + 9*30 = 830 kcal of macros vs 300 kcal stated -> OCR misread somewhere
    items = parse_nutrition_text("#jollibee", "Burger Steak 300 kcal\nProtein 60g Carbs 80g Fat 30g", matcher)
    assert items[0].is_suspect and "macro_energy_mismatch" in items[0].warnings


def test_atwater_consistency():
    assert macro_energy_consistent(540, 26, 50, 26)
    assert not macro_energy_consistent(300, 60, 80, 30)
    assert macro_energy_consistent(400, 20, None, None)  # partial macros below calories: fine
    assert not macro_energy_consistent(100, 50, None, None)  # protein alone 200 kcal > 100


def test_context_string_is_canonical():
    assert build_context_string("Jollibee", "Yumburger", "1 sandwich", 280, 12, 32, 11.5) == (
        "Yumburger from Jollibee (1 sandwich). 280 kcal; protein 12 g; carbs 32 g; fat 11.5 g."
    )
    assert build_context_string("7-Eleven", "Siopao", "1 pc", 290) == "Siopao from 7-Eleven (1 pc). 290 kcal."


def _text_image(lines, *, dark=False, size=(400, 200)) -> Image.Image:
    img = Image.new("RGB", size, "black" if dark else "white")
    draw = ImageDraw.Draw(img)
    for n, line in enumerate(lines):
        draw.text((10, 10 + 20 * n), line, fill="white" if dark else "black")
    return img


@pytest.mark.parametrize("dark", [False, True])
def test_preprocess_upscales_and_binarizes_to_dark_on_light(dark):
    out = preprocess_image(_text_image(["Yumburger 280 kcal"], dark=dark))
    assert out.mode == "L"
    assert min(out.size) >= 1000
    data = list(out.tobytes())
    pixels = set(data)
    assert pixels <= {0, 255}
    # background must end up white regardless of the source polarity
    assert data.count(255) > out.width * out.height / 2


def test_process_post_runs_ocr_on_each_image(matcher, monkeypatch, tmp_path):
    paths = []
    for i, line in enumerate(["Yumburger 280 kcal", "Jolly Spaghetti 480 kcal"]):
        p = tmp_path / f"ABC_{i}.jpg"
        _text_image([line]).save(p)
        paths.append(p)
    fake_ocr = {str(paths[0]): "Yumburger 280 kcal\nProtein 12g", str(paths[1]): "Jolly Spaghetti 480 kcal"}
    monkeypatch.setattr(ingestion_ocr, "ocr_image", lambda path: fake_ocr[str(path)])
    post = DownloadedPost("ABC", POST_URL, "#jollibee", paths)
    items = process_post(post, matcher)
    assert [(i.food_item, i.calories) for i in items] == [("Yumburger", 280), ("Jolly Spaghetti", 480)]


def test_process_post_survives_ocr_failure(matcher, monkeypatch, tmp_path):
    def boom(path):
        raise RuntimeError("tesseract crashed")

    monkeypatch.setattr(ingestion_ocr, "ocr_image", boom)
    post = DownloadedPost("ABC", POST_URL, "Jollibee Yumburger 280 kcal", [tmp_path / "x.jpg"])
    assert [i.calories for i in process_post(post, matcher)] == [280]  # caption still parsed


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_real_tesseract_roundtrip():  # pragma: no cover - only where tesseract exists
    from PIL import ImageFont

    img = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(img).text((10, 30), "Yumburger 280 kcal", fill="black", font=ImageFont.load_default(size=48))
    import pytesseract

    assert "280" in pytesseract.image_to_string(preprocess_image(img))


@pytest.mark.db
async def test_upsert_is_idempotent_and_never_overwrites_verified(db_session, matcher, embedder):
    from sqlalchemy import select, update

    from app.models import NutritionCatalog

    items = parse_nutrition_text("#jollibee", "Yumburger 280 kcal\nProtein 12g", matcher, source_post_url=POST_URL)
    await upsert_catalog_items(db_session, items, embedder)
    await upsert_catalog_items(db_session, items, embedder)
    rows = (await db_session.execute(select(NutritionCatalog))).scalars().all()
    assert len(rows) == 1 and rows[0].is_verified is False and rows[0].embedding is not None

    await db_session.execute(update(NutritionCatalog).values(is_verified=True, calories=290))
    await db_session.commit()
    items[0].calories = 999  # a later bad OCR read
    await upsert_catalog_items(db_session, items, embedder)
    db_session.expire_all()
    row = (await db_session.execute(select(NutritionCatalog))).scalar_one()
    assert row.calories == 290  # audited value preserved
