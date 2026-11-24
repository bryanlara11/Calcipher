"""Building blocks for @anongcaloriesnito ingestion: canonical context strings, OCR-text
parsing, image preprocessing, Tesseract OCR and catalog upserts.

The staged pipeline (scrape -> ocr -> parse -> review -> load) lives in `app/ingest.py`:
    python -m app.ingest --help
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.services.brand_matcher import BrandMatcher, normalize_brand

if TYPE_CHECKING:
    from PIL import Image as PILImage
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.embedder import Embedder

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "anongcaloriesnito"
MAX_CALORIES = 10_000
UNKNOWN_SERVING = "serving size unavailable"

# ---------------------------------------------------------------------------
# Canonical text used for embeddings (shared with seed.py — see AGENTS.md invariants)
# ---------------------------------------------------------------------------


def _fmt(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")


def build_context_string(
    brand_name: str,
    food_item: str,
    serving_size: str,
    calories: int,
    protein_g: float | Decimal | None = None,
    carbs_g: float | Decimal | None = None,
    fat_g: float | Decimal | None = None,
    price_php: float | Decimal | None = None,
) -> str:
    parts = [f"{food_item} from {brand_name} ({serving_size}). {calories} kcal"]
    for label, value in (("protein", protein_g), ("carbs", carbs_g), ("fat", fat_g)):
        formatted = _fmt(value)
        if formatted is not None:
            parts.append(f"{label} {formatted} g")
    if price_php is not None:
        parts.append(f"price PHP {price_php:.2f}")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# OCR text parsing
# ---------------------------------------------------------------------------

_NUM = r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?![\d,])|\d{1,5}(?:[.,]\d{1,2})?)(?![\d.,])"
_PRICE = re.compile(r"\+?(?:\bPHP|₱)\s*\d[\d,]*(?:\.\d{2})?", re.I)
_PRICE_VALUE = re.compile(r"(?:\bPHP|₱)\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d.,])", re.I)
# Digits OCR often confuses with letters, only when glued to a nutrition unit.
_OCR_DIGIT_TOKEN = re.compile(r"(?<![A-Za-z0-9])([0-9OoIlS]{2,5})(?=\s*(?:k?cal|calories|g\b))")
_OCR_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5"})

_CAL_PATTERNS = [
    re.compile(rf"{_NUM}\s*(?:kcals?|calories|calorie|cals?)\b", re.I),
    re.compile(rf"\b(?:calories|calorie|kcal|cals?|energy)\s*[:=\-–]?\s*{_NUM}", re.I),
]
_MACRO_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "protein_g": [
        re.compile(rf"\b(?:protein|prot|pro|p)\s*[:=\-–]?\s*{_NUM}\s*g", re.I),
        re.compile(rf"{_NUM}\s*g\s*(?:of\s+)?protein\b", re.I),
    ],
    "carbs_g": [
        re.compile(rf"\b(?:carbohydrates?|carbs?|c)\s*[:=\-–]?\s*{_NUM}\s*g", re.I),
        re.compile(rf"{_NUM}\s*g\s*(?:of\s+)?carb(?:ohydrate)?s?\b", re.I),
    ],
    "fat_g": [
        re.compile(rf"\b(?:total\s+fat|fats?|f)\s*[:=\-–]?\s*{_NUM}\s*g", re.I),
        re.compile(rf"{_NUM}\s*g\s*(?:of\s+)?fats?\b", re.I),
    ],
}
_SERVING_PATTERNS = [
    re.compile(r"\b(?:serving(?:\s+size)?|portion)\s*[:=\-–]\s*([^\n;|]{1,80})", re.I),
    re.compile(r"\((\d+(?:\.\d+)?\s*(?:g|ml|pcs?|pieces?|sticks?|cups?|slices?)\b[^)]{0,40})\)", re.I),
]
_HASHTAG_OR_MENTION = re.compile(r"[#@]([A-Za-z0-9_.]+)")
# "📍 Jollibee", "📍 Brand: X", "Brand: X", "Resto - X" (word labels need a separator)
_BRAND_LINE = re.compile(
    r"^\s*(?:📍\s*(?:(?:brand|resto|restaurant|from)\s*[:\-–]?\s*)?|(?:brand|resto|restaurant)\s*[:\-–]\s*)(.+)$",
    re.I,
)


@dataclass(slots=True)
class ParsedNutrition:
    brand_name: str
    brand_slug: str
    food_item: str
    calories: int
    serving_size: str = UNKNOWN_SERVING
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    source_post_url: str | None = None
    is_suspect: bool = False
    warnings: list[str] = field(default_factory=list)
    price_php: float | None = None
    price_source_url: str | None = None
    source_images: list[str] = field(default_factory=list)

    @property
    def context_string(self) -> str:
        return build_context_string(
            self.brand_name, self.food_item, self.serving_size, self.calories,
            self.protein_g, self.carbs_g, self.fat_g, self.price_php,
        )


def fix_ocr_digits(text: str) -> str:
    """'5O0 kcal' -> '500 kcal'. Only touches tokens that contain at least one real digit."""

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        return token.translate(_OCR_DIGIT_MAP) if any(ch.isdigit() for ch in token) else token

    return _OCR_DIGIT_TOKEN.sub(_sub, text)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw) else raw.replace(",", "."))


def _first(patterns: Sequence[re.Pattern[str]], text: str) -> re.Match[str] | None:
    """Earliest match across patterns (calories: the first figure on the line wins)."""
    hits = [m for p in patterns if (m := p.search(text))]
    return min(hits, key=lambda m: m.start()) if hits else None


def _by_priority(patterns: Sequence[re.Pattern[str]], text: str) -> re.Match[str] | None:
    """First pattern that matches. Macro patterns are ordered label-first ("Carbs: 50g") before
    unit-first ("50g carbs"), because in "Protein: 26g Carbs: 50g" the unit-first form would
    wrongly read "26g Carbs" as the carbs value."""
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m
    return None


def macro_energy_consistent(
    calories: float, protein_g: float | None, carbs_g: float | None, fat_g: float | None,
    tolerance: float = 0.25,
) -> bool:
    """Atwater check: 4P + 4C + 9F should be within ±tolerance of stated calories.

    Only evaluated when all three macros are known; partial macros are only checked for the
    impossible case where they alone exceed the calories by more than the tolerance.
    """
    known = [(protein_g, 4), (carbs_g, 4), (fat_g, 9)]
    energy = sum(v * k for v, k in known if v is not None)
    if all(v is not None for v, _ in known):
        if calories == 0:
            return energy == 0
        return abs(energy - calories) <= tolerance * calories
    return energy <= calories * (1 + tolerance)


def detect_brand(
    caption: str, ocr_text: str, matcher: BrandMatcher, display_names: Mapping[str, str] | None = None
) -> tuple[str, str] | None:
    """Return (brand_name, brand_slug). Prefers a known catalog brand; else an explicit brand line."""
    handles = " ".join(_HASHTAG_OR_MENTION.findall(caption))
    for blob in (ocr_text, handles, caption):
        slug = matcher.find_in_text(blob.replace("_", " "))
        if slug:
            return display_name(slug, display_names), slug
    for line in (caption + "\n" + ocr_text).splitlines():
        m = _BRAND_LINE.match(line)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
            if name and normalize_brand(name):
                return name[:255], normalize_brand(name)[:255]
    return None


KNOWN_BRAND_NAMES: dict[str, str] = {
    "andoks": "Andok's",
    "24 chicken": "24 Chicken",
    "mang inasal": "Mang Inasal",
    "mcdonalds": "McDonald's",
    "jollibee": "Jollibee",
    "chowking": "Chowking",
    "kenny rogers roasters": "Kenny Rogers Roasters",
    "subway": "Subway",
    "7 eleven": "7-Eleven",
    "uncle johns": "Uncle John's",
}


def display_name(slug: str, overrides: Mapping[str, str] | None = None) -> str:
    if overrides and slug in overrides:
        return overrides[slug]
    return KNOWN_BRAND_NAMES.get(slug, slug.title())


def _clean_item_name(text: str) -> str:
    text = re.sub(r"[#@][A-Za-z0-9_.]+", "", text)
    text = re.sub(r"[^\w\s&'().,/+-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–:|,.")
    return text[:255]


# Connector words only; case-insensitive, so no letter class here (it would match any word).
_CONTINUATION = re.compile(r"^(?:with|without|w/o|w/|and|plus|no|sans)\b", re.I)


def _is_name_continuation(line: str) -> bool:
    """A wrapped second line of an item name: starts lowercase or with a connector word."""
    return bool(line) and (
        line[0].islower() or line[0] in "(&+" or bool(_CONTINUATION.match(line))
        or line in {"McDo", "Sauce"}
    )


def _looks_like_item_name(line: str) -> bool:
    stripped = _clean_item_name(line)
    if len(stripped) < 3 or not re.search(r"[A-Za-z]{3,}", stripped):
        return False
    if _first(_CAL_PATTERNS, line) or any(_first(p, line) for p in _MACRO_PATTERNS.values()):
        return False
    serving = _first(_SERVING_PATTERNS, line)
    if serving and serving.start() == 0:
        return False
    return not _BRAND_LINE.match(line)


def parse_nutrition_text(
    caption: str,
    ocr_text: str,
    matcher: BrandMatcher,
    *,
    source_post_url: str | None = None,
    display_names: Mapping[str, str] | None = None,
    include_caption_values: bool = True,
) -> list[ParsedNutrition]:
    """Parse one post (caption + OCR of its images) into zero or more catalog rows.

    Each line holding a calorie value starts a record; the item name is the text before the
    calorie value on that line or the closest preceding name-like line; macro and serving lines
    that follow attach to the current record.
    """
    caption = caption or ""
    text = fix_ocr_digits(f"{ocr_text or ''}\n{caption if include_caption_values else ''}")
    brand = detect_brand(caption, ocr_text or "", matcher, display_names)
    if brand is None:
        logger.info("No brand found for post %s", source_post_url)
        return []
    brand_name, brand_slug = brand
    brand_tokens = set(brand_slug.split())

    records: list[dict[str, Any]] = []
    last_name: str | None = None
    prev_was_name = False  # was the previous non-blank line part of an item name?
    pending_serving: str | None = None
    for raw_line in text.splitlines():
        line = _PRICE.sub(" ", raw_line).strip()
        if not line:
            continue
        if re.search(r"-\d+(?:[.,]\d+)?\s*(?:k?cal|calories)\b|(?:calories|energy|kcal)\s*[:=]?\s*-\d", line, re.I):
            last_name = None
            continue
        cal = _first(_CAL_PATTERNS, line)
        if cal:
            raw_calories = _to_float(cal.group(1))
            calories = round(raw_calories)
            before = _clean_item_name(line[: cal.start()])
            name = before if _looks_like_item_name(before) else last_name
            records.append({"food_item": name, "calories": calories, "raw_calories": raw_calories, "line": line})
            prices = [_to_float(m.group(1)) for m in _PRICE_VALUE.finditer(raw_line)]
            if prices:
                records[-1]["price_php"] = prices[0]
                records[-1]["ambiguous_price"] = len(set(prices)) > 1
            if pending_serving:
                records[-1]["serving_size"] = pending_serving
            pending_serving = None
            last_name = None
            prev_was_name = False
        elif _looks_like_item_name(line):
            candidate = _clean_item_name(line)
            if set(normalize_brand(candidate).split()) - brand_tokens:  # not just the brand name
                # Graphics often wrap long names: "Chicken Inasal Pecho" / "without Rice".
                if prev_was_name and last_name and _is_name_continuation(candidate):
                    last_name = f"{last_name} {candidate}"[:255]
                else:
                    last_name = candidate
                prev_was_name = True
            else:
                prev_was_name = False
        else:
            prev_was_name = False
        serving = _first(_SERVING_PATTERNS, line)
        if serving and last_name:
            pending_serving = re.sub(r"\s+", " ", serving.group(1)).strip()[:100]
        if records and last_name is None:
            current = records[-1]
            for key, patterns in _MACRO_PATTERNS.items():
                m = _by_priority(patterns, line)
                if m and key not in current:
                    current[key] = _to_float(m.group(1))
            if serving and "serving_size" not in current:
                current["serving_size"] = re.sub(r"\s+", " ", serving.group(1)).strip()[:100]

    results: list[ParsedNutrition] = []
    seen: set[tuple[str, str]] = set()
    for rec in records:
        warnings: list[str] = []
        calories = rec["calories"]
        if not (0 <= calories <= MAX_CALORIES):
            logger.info("Dropping out-of-range calories %s (%s)", calories, source_post_url)
            continue
        food_item = rec.get("food_item")
        if not food_item:
            warnings.append("missing_food_item")
            continue
        item = ParsedNutrition(
            brand_name=brand_name,
            brand_slug=brand_slug,
            food_item=food_item,
            calories=calories,
            serving_size=rec.get("serving_size", UNKNOWN_SERVING),
            protein_g=rec.get("protein_g"),
            carbs_g=rec.get("carbs_g"),
            fat_g=rec.get("fat_g"),
            source_post_url=source_post_url,
            warnings=warnings,
            price_php=rec.get("price_php"),
            price_source_url=source_post_url if rec.get("price_php") is not None else None,
        )
        if item.serving_size == UNKNOWN_SERVING:
            item.warnings.append("missing_serving_size")
        if item.price_php is not None:
            item.warnings.append("price_requires_human_review")
        if rec.get("ambiguous_price"):
            item.is_suspect = True
            item.warnings.append("ambiguous_price")
        if not rec["raw_calories"].is_integer():
            item.is_suspect = True
            item.warnings.append("fractional_calories_require_review")
        matches = {round(_to_float(m.group(1))) for p in _CAL_PATTERNS for m in p.finditer(rec["line"])}
        if len(matches) > 1:
            item.is_suspect = True
            item.warnings.append("ambiguous_calories")
        for key in ("protein_g", "carbs_g", "fat_g"):
            value = getattr(item, key)
            if value is not None and not (0 <= value <= 1000):
                setattr(item, key, None)
                item.warnings.append(f"{key}_out_of_range")
        if not macro_energy_consistent(item.calories, item.protein_g, item.carbs_g, item.fat_g):
            item.is_suspect = True
            item.warnings.append("macro_energy_mismatch")
        key = (item.food_item.lower(), item.serving_size.lower())
        if key not in seen:
            seen.add(key)
            results.append(item)
        else:
            previous = next(i for i in results if (i.food_item.lower(), i.serving_size.lower()) == key)
            if any(getattr(previous, k) != getattr(item, k) for k in ("calories", "protein_g", "carbs_g", "fat_g", "price_php")):
                previous.is_suspect = True
                if "conflicting_source_values" not in previous.warnings:
                    previous.warnings.append("conflicting_source_values")
    return results


# ---------------------------------------------------------------------------
# Image preprocessing + OCR
# ---------------------------------------------------------------------------


def preprocess_image(image: PILImage.Image) -> PILImage.Image:
    """Grayscale -> upscale small images -> denoise -> Otsu binarisation (dark text on white)."""
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(image)
    gray = ImageOps.grayscale(image)
    if min(gray.size) < 1000:
        scale = min(8.0, 1000 / min(gray.size), 6000 / max(gray.size))
        gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)
    try:
        import cv2
        import numpy as np

        arr = np.array(gray)
        arr = cv2.medianBlur(arr, 3)
        _, binary = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if binary.mean() < 127:  # light text on dark background -> invert for Tesseract
            binary = 255 - binary
        return Image.fromarray(binary)
    except ImportError:  # Pillow-only fallback
        binary = gray.point(lambda p: 255 if p > 140 else 0, mode="L")
        if sum(binary.getdata()) / (binary.width * binary.height) < 127:
            binary = ImageOps.invert(binary)
        return binary


# Bump when preprocess_image or the Tesseract config changes, so cached OCR text is redone.
PREPROCESS_VERSION = "2"
TESSERACT_CONFIG = "--oem 3 --psm 6"
_WINDOWS_TESSERACT = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


class TesseractNotFoundError(RuntimeError):
    pass


def find_tesseract(configured: str | None = None) -> str | None:
    """Explicit setting -> PATH -> default Windows install locations."""
    if configured:
        return configured if Path(configured).is_file() or shutil.which(configured) else None
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    return next((str(p) for p in _WINDOWS_TESSERACT if p.is_file()), None)


def configure_tesseract(configured: str | None = None) -> str:
    import pytesseract

    from app.config import get_settings

    cmd = find_tesseract(configured or get_settings().tesseract_cmd)
    if cmd is None:
        raise TesseractNotFoundError(
            "Tesseract not found. Install it (macOS: brew install tesseract; Windows: UB Mannheim build) or set TESSERACT_CMD "
            "to the full path of tesseract.exe."
        )
    pytesseract.pytesseract.tesseract_cmd = cmd
    return cmd


def tesseract_config(psm: int = 6) -> str:
    if psm not in {3, 6, 11}:
        raise ValueError("OCR page segmentation must be 3 (automatic), 6 (block/table rows), or 11 (sparse text)")
    return f"--oem 3 --psm {psm}"


def tesseract_engine_id(lang: str, psm: int = 6) -> str:
    """Identifies everything that affects OCR output; part of the OCR cache key."""
    import pytesseract

    configure_tesseract()
    return f"tesseract-{pytesseract.get_tesseract_version()}|{lang}|{tesseract_config(psm)}|pre{PREPROCESS_VERSION}"


def ocr_image(path: str | Path, *, lang: str = "eng", psm: int = 6) -> str:
    import pytesseract
    from PIL import Image

    configure_tesseract()
    with Image.open(path) as img:
        prepared = preprocess_image(img)
    return pytesseract.image_to_string(prepared, lang=lang, config=tesseract_config(psm))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_UPDATABLE = ("brand_name", "calories", "protein_g", "carbs_g", "fat_g", "source_post_url",
              "context_string", "embedding", "embedding_model", "is_verified", "price_php", "price_source_url", "source_images")


async def upsert_catalog_items(
    session: AsyncSession,
    items: Sequence[ParsedNutrition],
    embedder: Embedder,
    *,
    verified: Sequence[bool] | None = None,
    commit: bool = True,
) -> int:
    """Insert or refresh catalog rows, embedding each with the current embedder.

    `verified[i]` marks row i as human-audited. Audited rows overwrite whatever is stored.
    Unaudited rows never overwrite a row that is already verified.
    """
    from sqlalchemy.dialects.postgresql import insert

    from app.models import NutritionCatalog

    if not items:
        return 0
    flags = list(verified) if verified is not None else [False] * len(items)
    contexts = [i.context_string for i in items]
    vectors = await asyncio.to_thread(embedder.embed, contexts)
    rows = [
        {
            "brand_name": i.brand_name,
            "brand_slug": i.brand_slug,
            "food_item": i.food_item,
            "calories": i.calories,
            "protein_g": i.protein_g,
            "carbs_g": i.carbs_g,
            "fat_g": i.fat_g,
            "price_php": i.price_php,
            "price_source_url": i.price_source_url,
            "source_images": i.source_images,
            "serving_size": i.serving_size,
            "is_verified": flag,
            "source_post_url": i.source_post_url,
            "context_string": ctx,
            "embedding": vec,
            "embedding_model": embedder.name,
        }
        for i, ctx, vec, flag in zip(items, contexts, vectors, flags, strict=True)
    ]
    written = 0
    for audited in (True, False):
        batch = [r for r in rows if r["is_verified"] is audited]
        if not batch:
            continue
        stmt = insert(NutritionCatalog).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_catalog_item",
            set_={col: stmt.excluded[col] for col in _UPDATABLE},
            where=None if audited else NutritionCatalog.is_verified.is_(False),
        ).returning(NutritionCatalog.id)
        written += len((await session.execute(stmt)).all())
    if commit:
        await session.commit()
    return written
