"""Staged @anongcaloriesnito ingestion pipeline. Each stage reads the previous stage's files
under `backend/data/` and can be re-run on its own:

    1. import-local authorized manifest + images -> data/raw (offline, recommended)
       scrape   authorized Instagram access -> data/raw (optional network alternative)
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

Use owner-supplied originals or an authorized export whenever possible. Academic use and a saved
login session do not grant permission to collect data. Stop on platform refusals; no evasion.
Parse regenerates parsed.csv as proposals while preserving reviewed.csv. To revisit a reviewed
post, compare the new proposals and edit your existing review rows after making a backup.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import random
import re
import shutil
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.config import BACKEND_DIR, get_settings
from app.services.brand_matcher import BrandMatcher, normalize_brand
from app.services.ingestion_ocr import (
    DEFAULT_PROFILE,
    KNOWN_BRAND_NAMES,
    MAX_CALORIES,
    PREPROCESS_VERSION,
    UNKNOWN_SERVING,
    ParsedNutrition,
    macro_energy_consistent,
    parse_nutrition_text,
)

logger = logging.getLogger("calcipher.ingest")

DEFAULT_DATA_DIR = BACKEND_DIR / "data"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
REVIEW_COLUMNS = [
    "action", "verified", "shortcode", "source_post_url", "brand_name", "brand_slug", "food_item",
    "serving_size", "calories", "protein_g", "carbs_g", "fat_g", "is_suspect", "warnings",
    "price_php", "price_source_url",
    "source_images", "provenance_json",
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
    source_type: str = "instagram"
    source_reference: str | None = None

    @staticmethod
    def path(raw_dir: Path, shortcode: str) -> Path:
        return raw_dir / f"{shortcode}.meta.json"

    def save(self, raw_dir: Path) -> None:
        path = self.path(raw_dir, self.shortcode)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> PostMeta:
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def known_shortcodes(raw_dir: Path) -> set[str]:
    return {p.name.removesuffix(".meta.json") for p in raw_dir.glob("*.meta.json")}


def post_images(raw_dir: Path, shortcode: str) -> list[str]:
    # Instaloader names single images "<sc>.jpg" and carousel slides "<sc>_1.jpg", "<sc>_2.jpg"...
    return sorted((
        p.name for p in raw_dir.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES
        and (p.stem == shortcode or re.fullmatch(re.escape(shortcode) + r"_\d+", p.stem))
    ), key=lambda name: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)])


def import_local(dirs: DataDirs, manifest: Path) -> dict[str, int]:
    """Import an owner-supplied manifest. Validate the entire batch before copying files.

    No network, implicit image discovery, or modification of previously imported posts.
    Images are ordered explicitly in the manifest and metadata is the completion marker.
    """
    from PIL import Image

    manifest = manifest.resolve()
    document = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("posts"), list):
        raise ValueError("manifest must contain a posts array")
    pending: list[tuple[PostMeta, list[Path]]] = []
    seen: set[str] = set()
    skipped = 0
    for entry in document["posts"]:
        if not isinstance(entry, dict):
            raise ValueError("each post must be an object")
        shortcode = entry.get("shortcode", "")
        if not isinstance(shortcode, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", shortcode):
            raise ValueError("shortcode must contain 1..100 letters, digits, underscores or hyphens")
        if shortcode in seen:
            raise ValueError(f"duplicate shortcode in manifest: {shortcode}")
        seen.add(shortcode)
        images = entry.get("images")
        if not isinstance(images, list) or not images:
            raise ValueError(f"{shortcode}: images must be a nonempty ordered array")
        sources = []
        names = []
        for index, image in enumerate(images, start=1):
            if not isinstance(image, str):
                raise ValueError(f"{shortcode}: image paths must be strings")
            source = (manifest.parent / image).resolve()
            if not source.is_relative_to(manifest.parent) or source.suffix.lower() not in IMAGE_SUFFIXES:
                raise ValueError(f"{shortcode}: image must be inside the manifest folder with a supported extension")
            with Image.open(source) as opened:
                opened.verify()
            sources.append(source)
            names.append(f"{shortcode}_{index}{source.suffix.lower()}")
        url = entry.get("url", "")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"{shortcode}: url is required (post URL or another provenance reference)")
        for key in ("caption", "profile", "date_utc", "source_reference"):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                raise ValueError(f"{shortcode}: {key} must be text")
        if entry.get("date_utc"):
            datetime.fromisoformat(entry["date_utc"].replace("Z", "+00:00"))
        meta = PostMeta(shortcode, url, entry.get("profile") or DEFAULT_PROFILE,
                        entry.get("date_utc"), entry.get("caption") or "", names,
                        source_type="local", source_reference=entry.get("source_reference"))
        existing = PostMeta.path(dirs.raw, shortcode)
        if existing.exists():
            old = PostMeta.load(existing)
            unchanged = asdict(old) == asdict(meta) and all(
                (dirs.raw / name).is_file() and _sha256(dirs.raw / name) == _sha256(source)
                for source, name in zip(sources, names, strict=True)
            )
            if not unchanged:
                raise ValueError(f"{shortcode}: conflicts with an existing post; existing data was preserved")
            skipped += 1
            continue
        for source, name in zip(sources, names, strict=True):
            target = dirs.raw / name
            if target.exists() and _sha256(target) != _sha256(source):
                raise ValueError(f"{shortcode}: conflicts with existing image {name}")
        pending.append((meta, sources))
    dirs.raw.mkdir(parents=True, exist_ok=True)
    for meta, sources in pending:
        for source, name in zip(sources, meta.images, strict=True):
            target = dirs.raw / name
            temporary = target.with_suffix(target.suffix + ".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        meta.save(dirs.raw)
    return {"imported_posts": len(pending), "skipped_known": skipped,
            "imported_images": sum(len(sources) for _, sources in pending)}


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
    known = {
        sc for sc in known_shortcodes(raw_dir)
        if (meta := PostMeta.load(PostMeta.path(raw_dir, sc))).images
        and all((raw_dir / name).is_file() for name in meta.images)
    }
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
                images = post_images(raw_dir, post.shortcode)
                expected = getattr(post, "mediacount", max(1, len(images)))
                if len(images) != expected:
                    raise ValueError(f"incomplete download: expected {expected} images, found {len(images)}")
                from PIL import Image
                for name in images:
                    with Image.open(raw_dir / name) as opened:
                        opened.verify()
                meta = PostMeta(
                    shortcode=post.shortcode,
                    url=f"https://www.instagram.com/p/{post.shortcode}/",
                    profile=profile,
                    date_utc=post.date_utc.isoformat() if post.date_utc else None,
                    caption=post.caption or "",
                    images=images,
                    is_video=bool(post.is_video),
                )
            except fatal:
                raise
            except Exception as exc:
                logger.warning("download failed for %s: %s", post.shortcode, exc)
                summary.failed.append(post.shortcode)
                continue
            meta.save(raw_dir)
            known.add(post.shortcode)
            summary.new_posts += 1
            logger.info("scraped %s (%d new)", post.shortcode, summary.new_posts)
            if summary.new_posts >= max_new:
                break
            sleeper(sleep_s + random.uniform(0, sleep_s / 2))  # polite pacing with jitter
    except fatal as exc:
        summary.stopped_early = f"{type(exc).__name__}: {exc}"
    return summary


def scrape(dirs: DataDirs, *, profile: str, max_new: int, login: str | None, sleep_s: float,
           session_file: Path | None = None) -> ScrapeSummary:
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
        max_connection_attempts=1,
        request_timeout=30.0,
    )
    fatal = (ie.AbortDownloadException, ie.TooManyRequestsException, ie.LoginRequiredException,
             ie.ConnectionException, ie.BadCredentialsException, ie.BadResponseException)
    if max_new <= 0:
        return ScrapeSummary()
    try:
        if session_file and not login:
            raise ValueError("--session-file requires --login")
        if login:
            loader.load_session_from_file(login, str(session_file) if session_file else None)
            if loader.test_login() != login:
                return ScrapeSummary(stopped_early="authentication_failed: saved session is expired or belongs to another account")
    except (OSError, ValueError, *fatal) as exc:
        return ScrapeSummary(stopped_early=f"session_error: {type(exc).__name__}; check your own session or use local import")

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
        fatal=fatal,
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


def read_ocr_text(ocr_dir: Path, image_name: str, raw_dir: Path | None = None) -> str | None:
    path = ocr_cache_path(ocr_dir, image_name)
    if not path.is_file():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        engine = cached.get("engine", "")
        if isinstance(engine, str) and engine.startswith("tesseract-") and not engine.endswith(f"|pre{PREPROCESS_VERSION}"):
            return None
        if raw_dir is not None and (not (raw_dir / image_name).is_file()
                                   or cached.get("sha256") != _sha256(raw_dir / image_name)):
            return None
        text = cached.get("text")
        return text if isinstance(text, str) else None
    except (ValueError, OSError, AttributeError):
        return None


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
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                cached = {}
            if (isinstance(cached, dict) and cached.get("sha256") == digest
                    and cached.get("engine") == engine_id and isinstance(cached.get("text"), str)):
                summary.cached += 1
                continue
        todo.append((image, digest))
        # A failed refresh must not leave an obsolete result available to parse.
        cache.unlink(missing_ok=True)

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
            cache = ocr_cache_path(dirs.ocr, image.name)
            temporary = cache.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"image": image.name, "sha256": digest, "engine": engine_id, "text": text},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(cache)
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
    columns = REVIEW_COLUMNS
    if append and exists:
        with path.open(encoding="utf-8-sig", newline="") as existing:
            columns = next(csv.reader(existing))
    with path.open("a" if append else "w", encoding="utf-8-sig" if not (append and exists) else "utf-8",
                   newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if not (append and exists):
            writer.writeheader()
        writer.writerows(rows)


def _fmt_num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _item_row(shortcode: str, item: ParsedNutrition) -> dict[str, str]:
    return {
        "action": "skip",  # a human must opt in to loading, even for plausible OCR
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
        "price_php": _fmt_num(item.price_php),
        "price_source_url": item.price_source_url or "",
        "source_images": ";".join(item.source_images),
        "provenance_json": json.dumps(item.provenance, ensure_ascii=False) if item.provenance else "",
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
            text = read_ocr_text(dirs.ocr, image, dirs.raw)
            if text is None:
                if meta.shortcode not in summary.posts_missing_ocr:
                    summary.posts_missing_ocr.append(meta.shortcode)
            else:
                texts.append((image, text))
        # Parse slides separately: state (item name, macros and serving) must never leak
        # across image boundaries. Caption values are parsed independently as well.
        items = []
        for image, text in [*texts, (None, "")]:
            parsed_items = parse_nutrition_text(
                meta.caption, text, matcher, source_post_url=meta.url, display_names=names,
                include_caption_values=not text,
            )
            for item in parsed_items:
                item.source_images = [image] if image else []
            items.extend(parsed_items)
        unique: dict[tuple[str, str, str], ParsedNutrition] = {}
        for item in items:
            key = (item.brand_slug, item.food_item.casefold(), (item.serving_size or "").casefold())
            if key in unique:
                old = unique[key]
                if any(getattr(old, k) != getattr(item, k)
                       for k in ("calories", "protein_g", "carbs_g", "fat_g", "price_php")):
                    old.is_suspect = True
                    if "conflicting_source_values" not in old.warnings:
                        old.warnings.append("conflicting_source_values")
                old.source_images = list(dict.fromkeys([*old.source_images, *item.source_images]))
            else:
                unique[key] = item
        items = list(unique.values())
        if items:
            rows = [_item_row(meta.shortcode, it) for it in items]
            summary.items += len(items)
        else:
            summary.posts_without_items.append(meta.shortcode)
            rows = [_empty_row(meta, "no_items_parsed")]
        all_rows.extend(rows)
        if meta.shortcode not in reviewed_posts and meta.shortcode not in summary.posts_missing_ocr:
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
                  integer: bool = False, required: bool = False, decimal_places: int | None = None) -> float | None:
    raw = raw.strip()
    if "," in raw and not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        errors.append(f"row {line}: {field_name} has ambiguous comma formatting; use a dot for decimals")
        return None
    raw = raw.replace(",", "")
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
    if decimal_places is not None and Decimal(raw).normalize().as_tuple().exponent < -decimal_places:
        errors.append(f"row {line}: {field_name} must have at most {decimal_places} decimal places")
        return None
    return value


def read_reviewed(path: Path) -> ReviewedItems:
    rows = read_csv_rows(path)
    out = ReviewedItems([], [], 0, [])
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            columns = set(next(csv.reader(fh), []))
        if not {"action", "verified", "brand_name", "food_item", "calories"} <= columns:
            out.errors.append("review CSV is missing required column headers")
            return out
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
            k: _parse_number(r.get(k, ""), k, line, out.errors, lo=0, hi=1000, decimal_places=2)
            for k in ("protein_g", "carbs_g", "fat_g")
        }
        price = _parse_number(r.get("price_php", ""), "price_php", line, out.errors,
                              lo=0, hi=99999999.99, decimal_places=2)
        price_source = r.get("price_source_url") or None
        if price is not None:
            parsed_url = urlparse(price_source or "")
            if not (parsed_url.scheme in {"http", "https"} and parsed_url.netloc):
                out.errors.append(f"row {line}: price_source_url must be an HTTP(S) source for the price")
            if verified_raw not in _YES:
                out.errors.append(f"row {line}: prices require human verification (verified=yes)")
        elif price_source:
            out.errors.append(f"row {line}: price_source_url requires price_php")
        if calories is not None and verified_raw not in _YES and (
            r.get("is_suspect", "").lower() in _YES
            or not macro_energy_consistent(calories, **macros)
        ):
            out.errors.append(f"row {line}: suspect nutrition requires correction or human verification")
        serving = r.get("serving_size") or None
        if serving == UNKNOWN_SERVING:
            serving = None
        provenance = {}
        if r.get("provenance_json"):
            try:
                provenance = json.loads(r["provenance_json"])
                if not isinstance(provenance, dict):
                    raise ValueError("expected an object")
                from app.services.source_review import validate_provenance
                out.errors.extend(f"row {line}: {error}" for error in validate_provenance(provenance, verified_raw in _YES))
            except (ValueError, TypeError) as exc:
                out.errors.append(f"row {line}: invalid provenance_json: {exc}")
                provenance = {}
        if len(food_item) > 255 or len(brand_name) > 255 or len(slug) > 255 or len(serving or "") > 100:
            out.errors.append(f"row {line}: text too long (food/brand max 255, serving max 100 chars)")
        key = (slug, food_item.lower(), (serving or "").lower())
        if key in seen:
            out.errors.append(f"row {line}: duplicate of row {seen[key]} (same brand, item and serving)")
        seen[key] = line
        if calories is None or not brand_name or not food_item:
            continue
        out.items.append(ParsedNutrition(
            brand_name=brand_name, brand_slug=slug, food_item=food_item, calories=int(calories),
            serving_size=serving, source_post_url=r.get("source_post_url") or None, **macros,
            price_php=price, price_source_url=price_source,
            source_images=[name.strip() for name in r.get("source_images", "").split(";") if name.strip()],
            provenance=provenance,
        ))
        out.verified.append(verified_raw in _YES)
    return out


async def load_reviewed(path: Path, *, dry_run: bool = False, allow_verified_updates: bool = False) -> dict[str, Any]:
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
            session, reviewed.items, get_embedder(), verified=reviewed.verified, commit=False, allow_verified_updates=allow_verified_updates
        )
        result["branches"] = await rematch_branches(session, await load_brand_matcher(session), commit=False)
        await session.commit()
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
        counts = (await session.execute(select(
            func.count().label("catalog_rows"),
            func.count().filter(NutritionCatalog.is_verified.is_(True)).label("approved_rows"),
            func.count().filter(NutritionCatalog.is_verified.is_(False)).label("pending_rows"),
            func.count().filter(NutritionCatalog.source_post_url.is_not(None)).label("source_backed_rows"),
            func.count(func.distinct(NutritionCatalog.brand_slug)).label("catalog_brands"),
            func.count(func.distinct(NutritionCatalog.brand_slug)).filter(NutritionCatalog.is_verified.is_(True)).label("approved_brands"),
            func.count(NutritionCatalog.calories).label("rows_with_calories"),
            func.count(NutritionCatalog.protein_g).label("rows_with_protein"),
            func.count().filter(NutritionCatalog.is_verified.is_(True), NutritionCatalog.price_php.is_not(None),
                                NutritionCatalog.price_source_url.is_not(None)).label("rows_with_checked_prices"),
        ).select_from(NutritionCatalog))).mappings().one()
        stale = await count_stale_embeddings(session, get_embedder().name)
    return {**dict(counts), "scraped_rows": counts["source_backed_rows"], "stale_embeddings": stale}



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

    p = sub.add_parser("import-local", help="import authorized local images and captions from a JSON manifest")
    p.add_argument("--manifest", type=Path, required=True)

    p = sub.add_parser("scrape", help="download new posts (images + caption) from Instagram")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--max-new", type=int, default=15, help="stop after this many NEW posts")
    p.add_argument("--login", help="Instagram username with a saved `instaloader --login` session")
    p.add_argument("--session-file", type=Path, help="path to your own saved session; requires --login")
    p.add_argument("--sleep", type=float, default=4.0, help="base seconds between posts (jittered)")

    p = sub.add_parser("ocr", help="run Tesseract on downloaded images (cached)")
    p.add_argument("--force", action="store_true", help="ignore the cache and redo every image")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--lang", default=None, help="Tesseract languages, e.g. eng or eng+fil")
    p.add_argument("--psm", type=int, choices=(3, 6, 11), default=6,
                   help="page segmentation: 3 automatic, 6 block/table rows, 11 sparse text")

    sub.add_parser("parse", help="turn caption + OCR text into review CSVs")

    p = sub.add_parser("load", help="validate reviewed.csv and upsert it into the database")
    p.add_argument("--file", type=Path, default=None, help="defaults to data/review/reviewed.csv")
    p.add_argument("--dry-run", action="store_true", help="validate and count only")
    p.add_argument("--allow-reviewed-updates", action="store_true", help="explicitly approve replacing existing reviewed corrections")

    p = sub.add_parser("reembed", help="recompute embeddings with the current embedder")
    p.add_argument("--all", action="store_true", help="re-embed every row, not only stale ones")

    sub.add_parser("status", help="show counts for every stage")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dirs = DataDirs(args.data_dir)
    settings = get_settings()

    if args.stage == "import-local":
        try:
            _print(import_local(dirs, args.manifest))
        except (ValueError, OSError) as exc:
            raise SystemExit(f"Import failed: {exc}") from None
    elif args.stage == "scrape":
        if args.sleep < 0 or args.max_new < 0:
            parser.error("--sleep and --max-new must be nonnegative")
        summary = scrape(dirs, profile=args.profile, max_new=args.max_new, login=args.login, sleep_s=args.sleep,
                         session_file=args.session_file)
        _print(summary)
        if summary.stopped_early:
            raise SystemExit("Scrape stopped. Follow any Instagram prompt, then retry later. "
                             "Completed posts will be skipped.")
        if summary.failed:
            raise SystemExit("Some downloads failed; incomplete posts remain retryable. Use local import if access is unavailable.")
    elif args.stage == "ocr":
        from app.services.ingestion_ocr import ocr_image, tesseract_engine_id

        lang = args.lang or settings.ocr_lang
        summary = run_ocr(dirs, ocr_fn=lambda path: ocr_image(path, lang=lang, psm=args.psm),
                          engine_id=tesseract_engine_id(lang, args.psm), force=args.force, workers=args.workers)
        _print(summary)
        if summary.failed:
            raise SystemExit("OCR failed for some images; fix the source files or Tesseract installation and retry.")
    elif args.stage == "parse":
        summary = run_parse(dirs)
        _print(summary)
        print(f"Review {dirs.reviewed_csv} (set action=load|skip, verified=yes|no), then run `load`.")
    elif args.stage == "load":
        ensure_selector_event_loop_policy()
        result = asyncio.run(load_reviewed(args.file or dirs.reviewed_csv, dry_run=args.dry_run, allow_verified_updates=args.allow_reviewed_updates))
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
