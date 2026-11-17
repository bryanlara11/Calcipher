"""Staged @anongcaloriesnito ingestion pipeline. Each stage reads the previous stage's files
under `backend/data/` and can be re-run on its own:

    1. scrape   Instagram -> data/raw/<shortcode>*.jpg + <shortcode>.meta.json   (network)
    2. ocr      data/raw images -> data/ocr/<image>.json (cached by image hash + engine)
    3. parse    meta + OCR text -> data/review/parsed.csv (always rewritten) and appends
                posts not yet reviewed to data/review/reviewed.csv (yours to edit)
    4. review   edit reviewed.csv by hand: fix values, set action=load|skip, verified=yes|no
    5. load     reviewed.csv -> nutrition_catalog (validated all-or-nothing, embedded),
                then re-matches cached map branches against the new brand list

    python -m app.ingest scrape --login <your_ig_username> --max-new 15
    python -m app.ingest ocr
    python -m app.ingest parse
    python -m app.ingest load --dry-run
    python -m app.ingest load
    python -m app.ingest reembed          # after changing EMBEDDER_BACKEND / EMBEDDING_MODEL
    python -m app.ingest status

Instagram throttles anonymous access and its terms prohibit automated scraping; log in once with
`instaloader --login <user>`, keep runs small, and use the data for academic research only.
To re-parse a post you already reviewed, delete its rows from reviewed.csv and run `parse`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import random
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.config import BACKEND_DIR, get_settings
from app.services.brand_matcher import BrandMatcher, normalize_brand
from app.services.ingestion_ocr import (
    DEFAULT_PROFILE,
    KNOWN_BRAND_NAMES,
    MAX_CALORIES,
    UNKNOWN_SERVING,
    ParsedNutrition,
    parse_nutrition_text,
)

logger = logging.getLogger("calcipher.ingest")

DEFAULT_DATA_DIR = BACKEND_DIR / "data"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
REVIEW_COLUMNS = [
    "action", "verified", "shortcode", "source_post_url", "brand_name", "brand_slug", "food_item",
    "serving_size", "calories", "protein_g", "carbs_g", "fat_g", "is_suspect", "warnings",
]
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0", ""}


@dataclass(frozen=True, slots=True)
class DataDirs:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def ocr(self) -> Path:
        return self.root / "ocr"

    @property
    def review(self) -> Path:
        return self.root / "review"

    @property
    def parsed_csv(self) -> Path:
        return self.review / "parsed.csv"

    @property
    def reviewed_csv(self) -> Path:
        return self.review / "reviewed.csv"


# ---------------------------------------------------------------------------
# Stage 1: scrape
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PostMeta:
    shortcode: str
    url: str
    profile: str
    date_utc: str | None
    caption: str
    images: list[str]
    is_video: bool = False

    @staticmethod
    def path(raw_dir: Path, shortcode: str) -> Path:
        return raw_dir / f"{shortcode}.meta.json"

    def save(self, raw_dir: Path) -> None:
        self.path(raw_dir, self.shortcode).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> PostMeta:
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def known_shortcodes(raw_dir: Path) -> set[str]:
    return {p.name.removesuffix(".meta.json") for p in raw_dir.glob("*.meta.json")}


def post_images(raw_dir: Path, shortcode: str) -> list[str]:
    # Instaloader names single images "<sc>.jpg" and carousel slides "<sc>_1.jpg", "<sc>_2.jpg"...
    return sorted(
        p.name for p in raw_dir.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES and (p.stem == shortcode or p.stem.startswith(f"{shortcode}_"))
    )


class PostLike(Protocol):
    shortcode: str
    caption: str | None
    date_utc: datetime | None
    is_video: bool


@dataclass(slots=True)
class ScrapeSummary:
    new_posts: int = 0
    skipped_known: int = 0
    failed: list[str] = field(default_factory=list)
    stopped_early: str | None = None


def scrape_posts(
    posts: Iterable[PostLike],
    download: Callable[[PostLike], None],
    raw_dir: Path,
    *,
    profile: str,
    max_new: int,
    sleep_s: float = 4.0,
    fatal: tuple[type[BaseException], ...] = (),
    sleeper: Callable[[float], None] = time.sleep,
) -> ScrapeSummary:
    """Download posts not seen before. The meta file is written LAST, so a post interrupted
    mid-download has no meta and is retried on the next run."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    known = known_shortcodes(raw_dir)
    summary = ScrapeSummary()
    if max_new <= 0:
        return summary
    try:
        for post in posts:
            if post.shortcode in known:
                summary.skipped_known += 1
                continue
            try:
                download(post)
            except fatal:
                raise
            except Exception as exc:
                logger.warning("download failed for %s: %s", post.shortcode, exc)
                summary.failed.append(post.shortcode)
                continue
            PostMeta(
                shortcode=post.shortcode,
                url=f"https://www.instagram.com/p/{post.shortcode}/",
                profile=profile,
                date_utc=post.date_utc.isoformat() if post.date_utc else None,
                caption=post.caption or "",
                images=post_images(raw_dir, post.shortcode),
                is_video=bool(post.is_video),
            ).save(raw_dir)
            summary.new_posts += 1
            logger.info("scraped %s (%d new)", post.shortcode, summary.new_posts)
            if summary.new_posts >= max_new:
                break
            sleeper(sleep_s + random.uniform(0, sleep_s / 2))  # polite pacing with jitter
    except fatal as exc:
        summary.stopped_early = f"{type(exc).__name__}: {exc}"
    return summary


def scrape(dirs: DataDirs, *, profile: str, max_new: int, login: str | None, sleep_s: float) -> ScrapeSummary:
    import instaloader
    from instaloader import exceptions as ie

    loader = instaloader.Instaloader(
        dirname_pattern=str(dirs.raw),
        filename_pattern="{shortcode}",
        download_videos=False,
        download_video_thumbnails=True,  # cover frames often carry the calorie text
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        quiet=True,
        fatal_status_codes=[401, 429],
        max_connection_attempts=3,
        request_timeout=30.0,
    )
    if login:
        loader.load_session_from_file(login)

    def posts():
        prof = instaloader.Profile.from_username(loader.context, profile)
        yield from prof.get_posts()

    return scrape_posts(
        posts(),
        lambda post: loader.download_post(post, target=profile),
        dirs.raw,
        profile=profile,
        max_new=max_new,
        sleep_s=sleep_s,
        fatal=(ie.AbortDownloadException, ie.TooManyRequestsException, ie.LoginRequiredException, ie.ConnectionException),
    )


# ---------------------------------------------------------------------------
# Stage 2: OCR (cached)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OcrSummary:
    processed: int = 0
    cached: int = 0
    failed: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ocr_cache_path(ocr_dir: Path, image_name: str) -> Path:
    return ocr_dir / f"{image_name}.json"


def read_ocr_text(ocr_dir: Path, image_name: str) -> str | None:
    path = ocr_cache_path(ocr_dir, image_name)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("text")


def run_ocr(
    dirs: DataDirs,
    *,
    ocr_fn: Callable[[Path], str],
    engine_id: str,
    force: bool = False,
    workers: int = 4,
) -> OcrSummary:
    """OCR every image once per (image content, engine). Re-runs are near-free."""
    dirs.ocr.mkdir(parents=True, exist_ok=True)
    summary = OcrSummary()
    todo: list[tuple[Path, str]] = []
    images = sorted(p for p in dirs.raw.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) if dirs.raw.exists() else []
    for image in images:
        digest = _sha256(image)
        cache = ocr_cache_path(dirs.ocr, image.name)
        if not force and cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("sha256") == digest and cached.get("engine") == engine_id:
                summary.cached += 1
                continue
        todo.append((image, digest))

    def work(item: tuple[Path, str]) -> tuple[Path, str, str | None, str | None]:
        image, digest = item
        try:
            return image, digest, ocr_fn(image), None
        except Exception as exc:
            return image, digest, None, f"{exc.__class__.__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:  # tesseract runs as a subprocess
        for image, digest, text, error in pool.map(work, todo):
            if error is not None:
                logger.warning("OCR failed for %s: %s", image.name, error)
                summary.failed.append(image.name)
                continue
            ocr_cache_path(dirs.ocr, image.name).write_text(
                json.dumps({"image": image.name, "sha256": digest, "engine": engine_id, "text": text},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary.processed += 1
    return summary


# ---------------------------------------------------------------------------
# Stage 3: parse -> review CSVs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ParseSummary:
    posts: int = 0
    items: int = 0
    posts_missing_ocr: list[str] = field(default_factory=list)
    posts_without_items: list[str] = field(default_factory=list)
    appended_posts: int = 0
    appended_rows: int = 0


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def write_csv_rows(path: Path, rows: Sequence[dict[str, Any]], *, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file() and path.stat().st_size > 0
    # utf-8-sig so Excel shows ñ / é correctly; reading uses utf-8-sig too.
    with path.open("a" if append else "w", encoding="utf-8-sig" if not (append and exists) else "utf-8",
                   newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        if not (append and exists):
            writer.writeheader()
        writer.writerows(rows)


def _fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _item_row(shortcode: str, item: ParsedNutrition) -> dict[str, str]:
    return {
        "action": "skip" if item.is_suspect else "load",
        "verified": "no",
        "shortcode": shortcode,
        "source_post_url": item.source_post_url or "",
        "brand_name": item.brand_name,
        "brand_slug": item.brand_slug,
        "food_item": item.food_item,
        "serving_size": item.serving_size,
        "calories": str(item.calories),
        "protein_g": _fmt_num(item.protein_g),
        "carbs_g": _fmt_num(item.carbs_g),
        "fat_g": _fmt_num(item.fat_g),
        "is_suspect": "yes" if item.is_suspect else "no",
        "warnings": ";".join(item.warnings),
    }


def _empty_row(meta: PostMeta, reason: str) -> dict[str, str]:
    """A placeholder so the reviewer sees posts that produced nothing and can fill them in."""
    row = dict.fromkeys(REVIEW_COLUMNS, "")
    row.update(action="skip", verified="no", shortcode=meta.shortcode, source_post_url=meta.url,
               is_suspect="no", warnings=reason)
    return row


def learned_brands(reviewed: Sequence[dict[str, str]]) -> dict[str, str]:
    """slug -> display name from rows a human already reviewed (teaches the parser new brands)."""
    out: dict[str, str] = {}
    for r in reviewed:
        slug = normalize_brand(r.get("brand_slug") or r.get("brand_name"))
        if slug and r.get("brand_name"):
            out.setdefault(slug, r["brand_name"])
    return out


def run_parse(dirs: DataDirs) -> ParseSummary:
    summary = ParseSummary()
    reviewed = read_csv_rows(dirs.reviewed_csv)
    reviewed_posts = {r.get("shortcode") for r in reviewed}
    names = {**KNOWN_BRAND_NAMES, **learned_brands(reviewed)}
    matcher = BrandMatcher(names.keys())

    metas = sorted(
        (PostMeta.load(p) for p in dirs.raw.glob("*.meta.json")) if dirs.raw.exists() else [],
        key=lambda m: (m.date_utc or "", m.shortcode),
    )
    all_rows: list[dict[str, str]] = []
    new_rows: list[dict[str, str]] = []
    for meta in metas:
        summary.posts += 1
        texts = []
        for image in meta.images:
            text = read_ocr_text(dirs.ocr, image)
            if text is None:
                if meta.shortcode not in summary.posts_missing_ocr:
                    summary.posts_missing_ocr.append(meta.shortcode)
            else:
                texts.append(text)
        items = parse_nutrition_text(
            meta.caption, "\n".join(texts), matcher, source_post_url=meta.url, display_names=names
        )
        if items:
            rows = [_item_row(meta.shortcode, it) for it in items]
            summary.items += len(items)
        else:
            summary.posts_without_items.append(meta.shortcode)
            rows = [_empty_row(meta, "no_items_parsed")]
        all_rows.extend(rows)
        if meta.shortcode not in reviewed_posts:
            new_rows.extend(rows)
            summary.appended_posts += 1

    write_csv_rows(dirs.parsed_csv, all_rows)
    if new_rows:
        write_csv_rows(dirs.reviewed_csv, new_rows, append=True)
    summary.appended_rows = len(new_rows)
    return summary


# ---------------------------------------------------------------------------
# Stage 5: load reviewed rows
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReviewedItems:
    items: list[ParsedNutrition]
    verified: list[bool]
    skipped: int
    errors: list[str]


def _parse_number(raw: str, field_name: str, line: int, errors: list[str], *, lo: float, hi: float,
                  integer: bool = False, required: bool = False) -> float | None:
    raw = raw.replace(",", "").strip()
    if not raw:
        if required:
            errors.append(f"row {line}: {field_name} is required")
        return None
    try:
        value = float(raw)
    except ValueError:
        errors.append(f"row {line}: {field_name} {raw!r} is not a number")
        return None
    if not (lo <= value <= hi):
        errors.append(f"row {line}: {field_name} {raw} outside {lo:g}..{hi:g}")
        return None
    if integer and not value.is_integer():
        errors.append(f"row {line}: {field_name} {raw} must be a whole number")
        return None
    return value


def read_reviewed(path: Path) -> ReviewedItems:
    rows = read_csv_rows(path)
    out = ReviewedItems([], [], 0, [])
    seen: dict[tuple[str, str, str], int] = {}
    for line, r in enumerate(rows, start=2):  # row 1 is the header
        action = r.get("action", "").lower()
        if action in ("skip", ""):
            out.skipped += 1
            continue
        if action != "load":
            out.errors.append(f"row {line}: action must be 'load' or 'skip', got {r.get('action')!r}")
            continue
        verified_raw = r.get("verified", "").lower()
        if verified_raw not in _YES | _NO:
            out.errors.append(f"row {line}: verified must be yes/no, got {r.get('verified')!r}")
        brand_name = r.get("brand_name", "")
        food_item = r.get("food_item", "")
        slug = normalize_brand(r.get("brand_slug") or brand_name)
        if not brand_name or not slug:
            out.errors.append(f"row {line}: brand_name is required")
        if not food_item:
            out.errors.append(f"row {line}: food_item is required")
        calories = _parse_number(r.get("calories", ""), "calories", line, out.errors,
                                 lo=0, hi=MAX_CALORIES, integer=True, required=True)
        macros = {
            k: _parse_number(r.get(k, ""), k, line, out.errors, lo=0, hi=1000)
            for k in ("protein_g", "carbs_g", "fat_g")
        }
        serving = r.get("serving_size") or UNKNOWN_SERVING
        if len(food_item) > 255 or len(brand_name) > 255 or len(serving) > 100:
            out.errors.append(f"row {line}: text too long (food/brand max 255, serving max 100 chars)")
        key = (slug, food_item.lower(), serving.lower())
        if key in seen:
            out.errors.append(f"row {line}: duplicate of row {seen[key]} (same brand, item and serving)")
        seen[key] = line
        if calories is None or not brand_name or not food_item:
            continue
        out.items.append(ParsedNutrition(
            brand_name=brand_name, brand_slug=slug, food_item=food_item, calories=int(calories),
            serving_size=serving, source_post_url=r.get("source_post_url") or None, **macros,
        ))
        out.verified.append(verified_raw in _YES)
    return out


async def load_reviewed(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    from app.db.maintenance import rematch_branches
    from app.db.session import get_engine, get_sessionmaker, init_db
    from app.services.brand_matcher import load_brand_matcher
    from app.services.embedder import get_embedder
    from app.services.ingestion_ocr import upsert_catalog_items

    if not path.is_file():
        raise SystemExit(f"{path} not found. Run `python -m app.ingest parse` first.")
    reviewed = read_reviewed(path)
    result: dict[str, Any] = {
        "to_load": len(reviewed.items),
        "verified": sum(reviewed.verified),
        "skipped": reviewed.skipped,
        "errors": reviewed.errors,
    }
    if reviewed.errors or dry_run or not reviewed.items:
        result["written"] = 0
        return result
    await init_db(get_engine())
    async with get_sessionmaker()() as session:
        result["written"] = await upsert_catalog_items(
            session, reviewed.items, get_embedder(), verified=reviewed.verified
        )
        result["branches"] = await rematch_branches(session, await load_brand_matcher(session))
    return result


# ---------------------------------------------------------------------------
# status / reembed
# ---------------------------------------------------------------------------


def local_status(dirs: DataDirs) -> dict[str, Any]:
    raw_posts = known_shortcodes(dirs.raw) if dirs.raw.exists() else set()
    images = [p for p in dirs.raw.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES] if dirs.raw.exists() else []
    ocr_done = sum(1 for p in images if ocr_cache_path(dirs.ocr, p.name).is_file())
    reviewed = read_csv_rows(dirs.reviewed_csv)
    return {
        "posts_scraped": len(raw_posts),
        "images": len(images),
        "images_with_ocr": ocr_done,
        "reviewed_rows": len(reviewed),
        "reviewed_load": sum(1 for r in reviewed if r.get("action", "").lower() == "load"),
        "reviewed_verified": sum(1 for r in reviewed if r.get("verified", "").lower() in _YES),
        "posts_not_in_review": len(raw_posts - {r.get("shortcode") for r in reviewed}),
    }


async def db_status() -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.db.maintenance import count_stale_embeddings
    from app.db.session import get_sessionmaker
    from app.models import NutritionCatalog
    from app.services.embedder import get_embedder

    async with get_sessionmaker()() as session:
        total = (await session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one()
        scraped = (await session.execute(
            select(func.count()).select_from(NutritionCatalog).where(NutritionCatalog.source_post_url.is_not(None))
        )).scalar_one()
        stale = await count_stale_embeddings(session, get_embedder().name)
    return {"catalog_rows": total, "scraped_rows": scraped, "stale_embeddings": stale}


async def reembed(only_stale: bool) -> int:
    from app.db.maintenance import reembed_catalog
    from app.db.session import get_engine, get_sessionmaker, init_db
    from app.services.embedder import get_embedder

    await init_db(get_engine())
    async with get_sessionmaker()() as session:
        return await reembed_catalog(session, get_embedder(), only_stale=only_stale)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print(obj: Any) -> None:
    print(json.dumps(obj if not hasattr(obj, "__dataclass_fields__") else asdict(obj), indent=2, default=str))


def main(argv: Sequence[str] | None = None) -> None:
    from app.db.session import ensure_selector_event_loop_policy

    parser = argparse.ArgumentParser(prog="python -m app.ingest", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    sub = parser.add_subparsers(dest="stage", required=True)

    p = sub.add_parser("scrape", help="download new posts (images + caption) from Instagram")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--max-new", type=int, default=15, help="stop after this many NEW posts")
    p.add_argument("--login", help="Instagram username with a saved `instaloader --login` session")
    p.add_argument("--sleep", type=float, default=4.0, help="base seconds between posts (jittered)")

    p = sub.add_parser("ocr", help="run Tesseract on downloaded images (cached)")
    p.add_argument("--force", action="store_true", help="ignore the cache and redo every image")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--lang", default=None, help="Tesseract languages, e.g. eng or eng+fil")

    sub.add_parser("parse", help="turn caption + OCR text into review CSVs")

    p = sub.add_parser("load", help="validate reviewed.csv and upsert it into the database")
    p.add_argument("--file", type=Path, default=None, help="defaults to data/review/reviewed.csv")
    p.add_argument("--dry-run", action="store_true", help="validate and count only")

    p = sub.add_parser("reembed", help="recompute embeddings with the current embedder")
    p.add_argument("--all", action="store_true", help="re-embed every row, not only stale ones")

    sub.add_parser("status", help="show counts for every stage")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dirs = DataDirs(args.data_dir)
    settings = get_settings()

    if args.stage == "scrape":
        summary = scrape(dirs, profile=args.profile, max_new=args.max_new, login=args.login, sleep_s=args.sleep)
        _print(summary)
        if summary.stopped_early:
            raise SystemExit("Scrape stopped. Follow any Instagram prompt, then retry later. "
                             "Completed posts will be skipped.")
    elif args.stage == "ocr":
        from app.services.ingestion_ocr import ocr_image, tesseract_engine_id

        lang = args.lang or settings.ocr_lang
        _print(run_ocr(dirs, ocr_fn=lambda path: ocr_image(path, lang=lang),
                       engine_id=tesseract_engine_id(lang), force=args.force, workers=args.workers))
    elif args.stage == "parse":
        summary = run_parse(dirs)
        _print(summary)
        print(f"Review {dirs.reviewed_csv} (set action=load|skip, verified=yes|no), then run `load`.")
    elif args.stage == "load":
        ensure_selector_event_loop_policy()
        result = asyncio.run(load_reviewed(args.file or dirs.reviewed_csv, dry_run=args.dry_run))
        _print(result)
        if result["errors"]:
            raise SystemExit(f"{len(result['errors'])} validation error(s); nothing was written.")
    elif args.stage == "reembed":
        ensure_selector_event_loop_policy()
        print(f"re-embedded {asyncio.run(reembed(only_stale=not args.all))} row(s)")
    elif args.stage == "status":
        status = local_status(dirs)
        ensure_selector_event_loop_policy()
        try:
            status.update(asyncio.run(db_status()))
        except Exception as exc:  # database not running: still show the file stages
            status["database"] = f"unavailable ({exc.__class__.__name__})"
        _print(status)


if __name__ == "__main__":
    main()
