import pytest
from PIL import Image, ImageDraw

from app.services.ingestion_ocr import (
    build_context_string,
    find_tesseract,
    fix_ocr_digits,
    macro_energy_consistent,
    parse_nutrition_text,
    preprocess_image,
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


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract binary not installed")
def test_real_tesseract_roundtrip(tmp_path, matcher):  # pragma: no cover - only where tesseract exists
    from PIL import ImageFont

    from app.services.ingestion_ocr import ocr_image

    img = Image.new("RGB", (1080, 400), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=56)
    draw.text((40, 40), "Chickenjoy with Rice", fill="black", font=font)
    draw.text((40, 140), "540 kcal", fill="black", font=font)
    draw.text((40, 240), "Protein 26g Carbs 50g Fat 26g", fill="black", font=font)
    path = tmp_path / "post.jpg"
    img.save(path)

    text = ocr_image(path)
    items = parse_nutrition_text("#jollibee", text, matcher)
    assert [(i.food_item, i.calories, i.protein_g) for i in items] == [("Chickenjoy with Rice", 540, 26.0)]


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


@pytest.mark.parametrize(
    ("ocr", "expected"),
    [
        ("Chicken Inasal Pecho\nwithout Rice\n360 kcal", "Chicken Inasal Pecho without Rice"),
        ("Chicken Inasal Pecho\n\nWithout Rice\n\n360 kcal", "Chicken Inasal Pecho Without Rice"),
        ("Burger Steak\n(with egg)\n470 kcal", "Burger Steak (with egg)"),
        ("Burger Steak\n(1 pc)\n470 kcal", "Burger Steak"),  # serving-size line, not part of the name
        # A new Title-case line is a new name, not a continuation of the previous one.
        ("Spicy Week Special\nBurger Steak\n470 kcal", "Burger Steak"),
        # Names never join across a calorie line.
        ("Yumburger 280 kcal\nwith fries\n450 kcal", "with fries"),
    ],
)
def test_wrapped_item_names(matcher, ocr, expected):
    items = parse_nutrition_text("#jollibee", ocr, matcher)
    assert items[-1].food_item == expected
