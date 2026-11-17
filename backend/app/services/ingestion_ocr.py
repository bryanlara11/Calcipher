"""@anongcaloriesnito ingestion: Instaloader download -> OpenCV preprocessing -> Tesseract OCR ->
nutrition parsing -> validation -> embedding -> idempotent upsert into `nutrition_catalog`.

CLI (run from backend/):
    python -m app.services.ingestion_ocr --max-posts 20 --dir data/raw            # download + ingest
    python -m app.services.ingestion_ocr --dir data/raw --skip-download --dry-run # re-parse local files

Instagram throttles anonymous scraping heavily. For more than a handful of posts, log in once with
`instaloader --login <your_username>` and pass `--login <your_username>` to reuse that session.
Use this for academic research only and respect Instagram's terms and rate limits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
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
UNKNOWN_SERVING = "1 serving"

# ---------------------------------------------------------------------------
# Canonical text used for embeddings (shared with seed.py — see AGENTS.md invariants)
# ---------------------------------------------------------------------------


def _fmt(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    f = float(value)
    return str(int(f)) if f.is_integer() else f"{f:.1f}"


def build_context_string(
    brand_name: str,
    food_item: str,
    serving_size: str,
    calories: int,
    protein_g: float | Decimal | None = None,
    carbs_g: float | Decimal | None = None,
    fat_g: float | Decimal | None = None,
) -> str:
    parts = [f"{food_item} from {brand_name} ({serving_size}). {calories} kcal"]
    for label, value in (("protein", protein_g), ("carbs", carbs_g), ("fat", fat_g)):
        formatted = _fmt(value)
        if formatted is not None:
            parts.append(f"{label} {formatted} g")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# OCR text parsing
# ---------------------------------------------------------------------------

_NUM = r"(\d{1,5}(?:[.,]\d{1,2})?)"
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

    @property
    def context_string(self) -> str:
        return build_context_string(
            self.brand_name, self.food_item, self.serving_size, self.calories,
            self.protein_g, self.carbs_g, self.fat_g,
        )


def fix_ocr_digits(text: str) -> str:
    """'5O0 kcal' -> '500 kcal'. Only touches tokens that contain at least one real digit."""

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        return token.translate(_OCR_DIGIT_MAP) if any(ch.isdigit() for ch in token) else token

    return _OCR_DIGIT_TOKEN.sub(_sub, text)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


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


def detect_brand(caption: str, ocr_text: str, matcher: BrandMatcher) -> tuple[str, str] | None:
    """Return (brand_name, brand_slug). Prefers a known catalog brand; else an explicit brand line."""
    handles = " ".join(_HASHTAG_OR_MENTION.findall(caption))
    for blob in (handles, caption, ocr_text):
        slug = matcher.find_in_text(blob.replace("_", " "))
        if slug:
            return _display_name(slug), slug
    for line in (caption + "\n" + ocr_text).splitlines():
        m = _BRAND_LINE.match(line)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
            if name and normalize_brand(name):
                return name[:255], normalize_brand(name)[:255]
    return None


_DISPLAY_NAMES = {
    "andoks": "Andok's",
    "24 chicken": "24 Chicken",
    "mang inasal": "Mang Inasal",
    "jollibee": "Jollibee",
    "chowking": "Chowking",
    "kenny rogers roasters": "Kenny Rogers Roasters",
    "subway": "Subway",
    "7 eleven": "7-Eleven",
    "uncle johns": "Uncle John's",
}


def _display_name(slug: str) -> str:
    return _DISPLAY_NAMES.get(slug, slug.title())


def _clean_item_name(text: str) -> str:
    text = re.sub(r"[#@][A-Za-z0-9_.]+", "", text)
    text = re.sub(r"[^\w\s&'().,/+-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–:|,.")
    return text[:255]


def _looks_like_item_name(line: str) -> bool:
    stripped = _clean_item_name(line)
    if len(stripped) < 3 or not re.search(r"[A-Za-z]{3,}", stripped):
        return False
    if _first(_CAL_PATTERNS, line) or any(_first(p, line) for p in _MACRO_PATTERNS.values()):
        return False
    return not _BRAND_LINE.match(line)


def parse_nutrition_text(
    caption: str,
    ocr_text: str,
    matcher: BrandMatcher,
    *,
    source_post_url: str | None = None,
) -> list[ParsedNutrition]:
    """Parse one post (caption + OCR of its images) into zero or more catalog rows.

    Each line holding a calorie value starts a record; the item name is the text before the
    calorie value on that line or the closest preceding name-like line; macro and serving lines
    that follow attach to the current record.
    """
    caption = caption or ""
    text = fix_ocr_digits(f"{ocr_text or ''}\n{caption}")
    brand = detect_brand(caption, ocr_text or "", matcher)
    if brand is None:
        logger.info("No brand found for post %s", source_post_url)
        return []
    brand_name, brand_slug = brand
    brand_tokens = set(brand_slug.split())

    records: list[dict[str, Any]] = []
    last_name: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cal = _first(_CAL_PATTERNS, line)
        if cal:
            calories = round(_to_float(cal.group(1)))
            before = _clean_item_name(line[: cal.start()])
            name = before if _looks_like_item_name(before) else last_name
            records.append({"food_item": name, "calories": calories, "line": line})
        elif _looks_like_item_name(line):
            candidate = _clean_item_name(line)
            if set(normalize_brand(candidate).split()) - brand_tokens:  # not just the brand name
                last_name = candidate
        if records:
            current = records[-1]
            for key, patterns in _MACRO_PATTERNS.items():
                m = _by_priority(patterns, line)
                if m and key not in current:
                    current[key] = _to_float(m.group(1))
            serving = _first(_SERVING_PATTERNS, line)
            if serving and "serving_size" not in current:
                current["serving_size"] = re.sub(r"\s+", " ", serving.group(1)).strip()[:100]

    results: list[ParsedNutrition] = []
    seen: set[tuple[str, str]] = set()
    for rec in records:
        warnings: list[str] = []
        calories = rec["calories"]
        if not (0 < calories <= MAX_CALORIES):
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
        )
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
    return results


# ---------------------------------------------------------------------------
# Image preprocessing + OCR
# ---------------------------------------------------------------------------


def preprocess_image(image: PILImage.Image) -> PILImage.Image:
    """Grayscale -> upscale small images -> denoise -> Otsu binarisation (dark text on white)."""
    from PIL import Image, ImageOps

    gray = ImageOps.grayscale(image)
    if min(gray.size) < 1000:
        scale = 1000 / min(gray.size)
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


def ocr_image(path: str | Path, *, lang: str = "eng") -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        prepared = preprocess_image(img.convert("RGB"))
    return pytesseract.image_to_string(prepared, lang=lang, config="--oem 3 --psm 6")


# ---------------------------------------------------------------------------
# Instaloader download
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DownloadedPost:
    shortcode: str
    url: str
    caption: str
    image_paths: list[Path]


def download_posts(
    target_dir: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    max_posts: int = 20,
    login: str | None = None,
) -> Iterator[DownloadedPost]:
    import instaloader

    target_dir.mkdir(parents=True, exist_ok=True)
    loader = instaloader.Instaloader(
        dirname_pattern=str(target_dir),
        filename_pattern="{shortcode}",
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        quiet=True,
        max_connection_attempts=3,
        request_timeout=30.0,
    )
    if login:
        loader.load_session_from_file(login)
    prof = instaloader.Profile.from_username(loader.context, profile)
    for index, post in enumerate(prof.get_posts()):
        if index >= max_posts:
            break
        caption = post.caption or ""
        if not list(target_dir.glob(f"{post.shortcode}*.jpg")):
            loader.download_post(post, target=profile)
        (target_dir / f"{post.shortcode}.caption.txt").write_text(caption, encoding="utf-8")
        yield DownloadedPost(
            shortcode=post.shortcode,
            url=f"https://www.instagram.com/p/{post.shortcode}/",
            caption=caption,
            image_paths=sorted(target_dir.glob(f"{post.shortcode}*.jpg")),
        )


def iter_local_posts(target_dir: Path) -> Iterator[DownloadedPost]:
    """Re-parse previously downloaded posts without touching the network."""
    for caption_file in sorted(target_dir.glob("*.caption.txt")):
        shortcode = caption_file.name.removesuffix(".caption.txt")
        yield DownloadedPost(
            shortcode=shortcode,
            url=f"https://www.instagram.com/p/{shortcode}/",
            caption=caption_file.read_text(encoding="utf-8"),
            image_paths=sorted(target_dir.glob(f"{shortcode}*.jpg")),
        )


def process_post(post: DownloadedPost, matcher: BrandMatcher) -> list[ParsedNutrition]:
    ocr_chunks = []
    for path in post.image_paths:
        try:
            ocr_chunks.append(ocr_image(path))
        except Exception:
            logger.exception("OCR failed for %s", path)
    return parse_nutrition_text(post.caption, "\n".join(ocr_chunks), matcher, source_post_url=post.url)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def upsert_catalog_items(
    session: AsyncSession, items: Sequence[ParsedNutrition], embedder: Embedder
) -> int:
    """Insert or refresh rows. Never overwrites rows that were manually verified."""
    from sqlalchemy.dialects.postgresql import insert

    from app.models import NutritionCatalog

    if not items:
        return 0
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
            "serving_size": i.serving_size,
            "is_verified": False,
            "source_post_url": i.source_post_url,
            "context_string": ctx,
            "embedding": vec,
        }
        for i, ctx, vec in zip(items, contexts, vectors, strict=True)
    ]
    stmt = insert(NutritionCatalog).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_catalog_item",
        set_={
            col: stmt.excluded[col]
            for col in ("brand_name", "calories", "protein_g", "carbs_g", "fat_g",
                        "source_post_url", "context_string", "embedding")
        },
        where=NutritionCatalog.is_verified.is_(False),
    ).returning(NutritionCatalog.id)
    written = len((await session.execute(stmt)).all())
    await session.commit()
    return written


async def _run_cli(args: argparse.Namespace) -> None:
    from app.db.session import get_sessionmaker, init_db, get_engine
    from app.services.brand_matcher import load_brand_matcher
    from app.services.embedder import get_embedder

    target = Path(args.dir)
    await init_db(get_engine())
    async with get_sessionmaker()() as session:
        db_matcher = await load_brand_matcher(session)
        matcher = BrandMatcher(set(db_matcher.slugs) | set(_DISPLAY_NAMES))
        posts = (
            iter_local_posts(target)
            if args.skip_download
            else download_posts(target, profile=args.profile, max_posts=args.max_posts, login=args.login)
        )
        parsed: list[ParsedNutrition] = []
        for post in posts:
            items = process_post(post, matcher)
            logger.info("%s -> %d item(s)", post.shortcode, len(items))
            parsed.extend(items)
        clean = [p for p in parsed if not p.is_suspect or args.keep_suspect]
        print(json.dumps([asdict(p) for p in parsed], indent=2, default=str))
        if args.dry_run:
            print(f"[dry-run] parsed {len(parsed)} item(s), {len(clean)} would be stored")
            return
        stored = await upsert_catalog_items(session, clean, get_embedder())
        print(f"stored/updated {stored} item(s); skipped {len(parsed) - len(clean)} suspect")


def main() -> None:
    from app.db.session import ensure_selector_event_loop_policy

    parser = argparse.ArgumentParser(description="Ingest @anongcaloriesnito posts via OCR")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--max-posts", type=int, default=20)
    parser.add_argument("--dir", default="data/raw")
    parser.add_argument("--login", default=None, help="Instagram username with a saved instaloader session")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-suspect", action="store_true", help="store rows failing the Atwater check")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_selector_event_loop_policy()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
