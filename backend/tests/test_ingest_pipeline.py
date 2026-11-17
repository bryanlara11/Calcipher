"""Staged ingestion (scrape -> ocr -> parse -> review -> load) and the catalog-maintenance gaps:
demo-data toggles, re-embedding across embedder changes, and branch re-matching."""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.db import seed as seed_module
from app.db.maintenance import count_stale_embeddings, reembed_catalog, rematch_branches
from app.ingest import (
    REVIEW_COLUMNS,
    DataDirs,
    PostMeta,
    load_reviewed,
    local_status,
    read_csv_rows,
    read_reviewed,
    run_ocr,
    run_parse,
    scrape_posts,
    write_csv_rows,
)
from app.models import NutritionCatalog, OsmBranchCache
from app.services.brand_matcher import BrandMatcher, BrandRegistry
from app.services.embedder import HashingEmbedder
from tests.conftest import SEED_SLUGS, add_branch, add_item

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class FakePost:
    shortcode: str
    caption: str | None = ""
    date_utc: datetime | None = datetime(2026, 1, 1, tzinfo=timezone.utc)
    is_video: bool = False


def _fake_downloader(raw_dir, n_images=1):
    def download(post):
        for i in range(n_images):
            name = f"{post.shortcode}.jpg" if n_images == 1 else f"{post.shortcode}_{i + 1}.jpg"
            Image.new("RGB", (40, 40), "white").save(raw_dir / name)
    return download


def _add_post(dirs: DataDirs, shortcode: str, caption: str, ocr_texts: list[str] | None):
    """Simulate scrape (+ optionally ocr) output on disk."""
    dirs.raw.mkdir(parents=True, exist_ok=True)
    images = []
    for i, _ in enumerate(ocr_texts or [""]):
        name = f"{shortcode}_{i + 1}.jpg"
        Image.new("RGB", (40, 40), (i, i, i)).save(dirs.raw / name)
        images.append(name)
    PostMeta(shortcode, f"https://www.instagram.com/p/{shortcode}/", "anongcaloriesnito",
             "2026-01-01T00:00:00+00:00", caption, images).save(dirs.raw)
    if ocr_texts is not None:
        lookup = dict(zip(images, ocr_texts))
        run_ocr(dirs, ocr_fn=lambda p: lookup[p.name], engine_id="test-engine")


@pytest.fixture
def dirs(tmp_path) -> DataDirs:
    return DataDirs(tmp_path / "data")


def _review_row(**overrides) -> dict[str, str]:
    row = dict.fromkeys(REVIEW_COLUMNS, "")
    row.update(action="load", verified="no", shortcode="SC1", brand_name="Jollibee", brand_slug="jollibee",
               food_item="Yumburger", serving_size="1 sandwich", calories="280", protein_g="12")
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Stage 1: scrape
# ---------------------------------------------------------------------------


def test_scrape_skips_known_posts_and_caps_new(dirs):
    _add_post(dirs, "OLD1", "old caption", None)
    posts = [FakePost("NEW1", "Jollibee Yumburger 280 kcal"), FakePost("OLD1"), FakePost("NEW2"), FakePost("NEW3")]
    summary = scrape_posts(posts, _fake_downloader(dirs.raw, n_images=2), dirs.raw,
                           profile="anongcaloriesnito", max_new=2, sleeper=lambda s: None)
    assert (summary.new_posts, summary.skipped_known) == (2, 1)
    meta = PostMeta.load(dirs.raw / "NEW1.meta.json")
    assert meta.images == ["NEW1_1.jpg", "NEW1_2.jpg"]
    assert meta.caption == "Jollibee Yumburger 280 kcal"
    assert meta.url == "https://www.instagram.com/p/NEW1/"
    assert not (dirs.raw / "NEW3.meta.json").exists()


def test_scrape_failed_download_leaves_no_meta_so_it_retries(dirs):
    def flaky(post):
        if post.shortcode == "BAD":
            raise OSError("connection reset")
        _fake_downloader(dirs.raw)(post)

    summary = scrape_posts([FakePost("BAD"), FakePost("OK")], flaky, dirs.raw,
                           profile="p", max_new=5, sleeper=lambda s: None)
    assert summary.failed == ["BAD"] and summary.new_posts == 1
    assert not (dirs.raw / "BAD.meta.json").exists()


def test_scrape_stops_on_rate_limit(dirs):
    class TooMany(Exception):
        pass

    def limited(post):
        raise TooMany("429")

    summary = scrape_posts([FakePost("A"), FakePost("B")], limited, dirs.raw, profile="p", max_new=5,
                           fatal=(TooMany,), sleeper=lambda s: None)
    assert summary.stopped_early.startswith("TooMany") and summary.new_posts == 0


# ---------------------------------------------------------------------------
# Stage 2: OCR cache
# ---------------------------------------------------------------------------


def test_ocr_runs_once_per_image_and_engine(dirs):
    _add_post(dirs, "SC1", "", None)
    calls = []

    def fake_ocr(path):
        calls.append(path.name)
        return "Yumburger 280 kcal"

    first = run_ocr(dirs, ocr_fn=fake_ocr, engine_id="e1")
    second = run_ocr(dirs, ocr_fn=fake_ocr, engine_id="e1")
    assert (first.processed, second.processed, second.cached) == (1, 0, 1)

    Image.new("RGB", (40, 40), "white").save(dirs.raw / "SC1_1.jpg")  # image content changed
    assert run_ocr(dirs, ocr_fn=fake_ocr, engine_id="e1").processed == 1
    assert run_ocr(dirs, ocr_fn=fake_ocr, engine_id="e2").processed == 1  # new engine/config
    assert run_ocr(dirs, ocr_fn=fake_ocr, engine_id="e2", force=True).processed == 1
    assert len(calls) == 4


def test_ocr_failure_is_not_cached(dirs):
    _add_post(dirs, "SC1", "", None)

    def boom(path):
        raise RuntimeError("tesseract crashed")

    summary = run_ocr(dirs, ocr_fn=boom, engine_id="e1")
    assert summary.failed == ["SC1_1.jpg"]
    assert run_ocr(dirs, ocr_fn=lambda p: "ok", engine_id="e1").processed == 1  # retried


# ---------------------------------------------------------------------------
# Stage 3: parse -> review CSVs
# ---------------------------------------------------------------------------


def test_parse_writes_review_files(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Yumburger 280 kcal\nProtein 12g", "Jolly Spaghetti 480 kcal"])
    _add_post(dirs, "SC2", "just vibes", ["nothing useful here"])
    summary = run_parse(dirs)
    assert (summary.posts, summary.items, summary.appended_posts) == (2, 2, 2)
    assert summary.posts_without_items == ["SC2"]

    rows = read_csv_rows(dirs.reviewed_csv)
    assert [(r["shortcode"], r["food_item"], r["calories"], r["action"]) for r in rows] == [
        ("SC1", "Yumburger", "280", "load"),
        ("SC1", "Jolly Spaghetti", "480", "load"),
        ("SC2", "", "", "skip"),  # placeholder so the reviewer sees the empty post
    ]
    assert rows[0]["protein_g"] == "12" and rows[0]["verified"] == "no"
    assert read_csv_rows(dirs.parsed_csv) == rows


def test_parse_never_overwrites_reviewed_rows(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Yumburger 280 kcal"])
    run_parse(dirs)
    rows = read_csv_rows(dirs.reviewed_csv)
    rows[0].update(calories="290", verified="yes")  # human correction
    write_csv_rows(dirs.reviewed_csv, rows)

    _add_post(dirs, "SC2", "#subway", ["Turkey Breast Sub 280 kcal"])
    summary = run_parse(dirs)
    assert summary.appended_posts == 1
    rows = read_csv_rows(dirs.reviewed_csv)
    assert [(r["shortcode"], r["calories"], r["verified"]) for r in rows] == [
        ("SC1", "290", "yes"), ("SC2", "280", "no"),
    ]
    assert run_parse(dirs).appended_rows == 0  # idempotent


def test_parse_flags_missing_ocr(dirs):
    _add_post(dirs, "SC1", "Jollibee Yumburger 280 kcal", None)  # scraped, OCR not run
    summary = run_parse(dirs)
    assert summary.posts_missing_ocr == ["SC1"]
    assert summary.items == 1  # the caption alone still parsed


def test_parse_learns_brands_from_reviewed_csv(dirs):
    write_csv_rows(dirs.reviewed_csv, [_review_row(shortcode="OLD", brand_name="Army Navy",
                                                    brand_slug="army navy", food_item="Burrito")])
    _add_post(dirs, "SC1", "Lunch at #armynavy", ["Army Navy\nSteak Burrito 640 kcal"])
    run_parse(dirs)
    new = [r for r in read_csv_rows(dirs.reviewed_csv) if r["shortcode"] == "SC1"]
    assert [(r["brand_name"], r["brand_slug"], r["food_item"]) for r in new] == [
        ("Army Navy", "army navy", "Steak Burrito")
    ]


def test_review_csv_round_trips_non_ascii(dirs):
    write_csv_rows(dirs.reviewed_csv, [_review_row(food_item="Café Ñora Sisig")])
    assert read_csv_rows(dirs.reviewed_csv)[0]["food_item"] == "Café Ñora Sisig"
    assert dirs.reviewed_csv.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8


# ---------------------------------------------------------------------------
# Stage 5: load (validation)
# ---------------------------------------------------------------------------


def test_read_reviewed_validates_everything(dirs):
    write_csv_rows(dirs.reviewed_csv, [
        _review_row(),
        _review_row(action="skip", calories="not a number"),       # skipped rows aren't validated
        _review_row(food_item="Bad Cal", calories="abc"),
        _review_row(food_item="Too Much", calories="20000"),
        _review_row(food_item="Neg Protein", protein_g="-5"),
        _review_row(food_item="", calories="100"),
        _review_row(food_item="Yumburger"),                          # duplicate of row 2
        _review_row(food_item="Weird", action="maybe"),
        _review_row(food_item="Flag", verified="perhaps"),
        _review_row(food_item="Fraction", calories="280.5"),
    ])
    result = read_reviewed(dirs.reviewed_csv)
    joined = "\n".join(result.errors)
    for expected in ("row 4: calories 'abc' is not a number", "row 5: calories 20000 outside",
                     "row 6: protein_g -5 outside", "row 7: food_item is required",
                     "row 8: duplicate of row 2", "row 9: action must be", "row 10: verified must be",
                     "row 11: calories 280.5 must be a whole number"):
        assert expected in joined, expected
    assert result.skipped == 1


def test_read_reviewed_accepts_clean_rows(dirs):
    write_csv_rows(dirs.reviewed_csv, [
        _review_row(verified="YES", calories="1,200", brand_slug=""),  # slug derived from name
        _review_row(food_item="Spaghetti", serving_size="", protein_g="", source_post_url="https://x/p/1/"),
    ])
    result = read_reviewed(dirs.reviewed_csv)
    assert result.errors == []
    assert result.verified == [True, False]
    a, b = result.items
    assert (a.brand_slug, a.calories, a.protein_g) == ("jollibee", 1200, 12.0)
    assert (b.serving_size, b.protein_g, b.source_post_url) == ("1 serving", None, "https://x/p/1/")


@pytest.mark.db
async def test_load_is_all_or_nothing(dirs, db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    write_csv_rows(dirs.reviewed_csv, [_review_row(), _review_row(food_item="Bad", calories="x")])
    result = await load_reviewed(dirs.reviewed_csv)
    assert result["errors"] and result["written"] == 0
    count = (await db_session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one()
    assert count == 0


@pytest.mark.db
async def test_load_upserts_and_rematches_branches(dirs, db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    await add_branch(db_session, osm_id=1, poi_name="Army Navy - Taft", lat=14.57, lng=120.98, slug=None)
    write_csv_rows(dirs.reviewed_csv, [
        _review_row(brand_name="Army Navy", brand_slug="army navy", food_item="Steak Burrito",
                    calories="640", source_post_url="https://www.instagram.com/p/SC1/", verified="yes"),
        _review_row(food_item="Yumburger", calories="280"),
    ])
    result = await load_reviewed(dirs.reviewed_csv)
    assert result["errors"] == [] and result["written"] == 2
    assert result["branches"]["newly_matched"] == 1  # the grey Army Navy pin now has data

    rows = {r.food_item: r for r in (await db_session.execute(select(NutritionCatalog))).scalars()}
    assert rows["Steak Burrito"].is_verified is True
    assert rows["Steak Burrito"].embedding_model == "hash-768"
    assert rows["Yumburger"].is_verified is False
    branch = await db_session.get(OsmBranchCache, ("node", 1))
    await db_session.refresh(branch)
    assert branch.matched_brand_slug == "army navy"


@pytest.mark.db
async def test_unverified_load_never_overwrites_verified_row(dirs, db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    write_csv_rows(dirs.reviewed_csv, [_review_row(calories="290", verified="yes")])
    await load_reviewed(dirs.reviewed_csv)
    write_csv_rows(dirs.reviewed_csv, [_review_row(calories="999", verified="no")])
    await load_reviewed(dirs.reviewed_csv)
    write_csv_rows(dirs.reviewed_csv, [_review_row(calories="300", verified="yes")])  # human re-audit wins
    await load_reviewed(dirs.reviewed_csv)
    row = (await db_session.execute(select(NutritionCatalog))).scalar_one()
    await db_session.refresh(row)
    assert (row.calories, row.is_verified) == (300, True)


def _use_test_db(monkeypatch, pg_url):
    """Point the app's engine (used by load_reviewed / run_seed) at the test database."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import get_settings
    from app.db import session as session_module

    # monkeypatch restores all of this at teardown; NullPool leaves no open connections behind.
    monkeypatch.setenv("DATABASE_URL", pg_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", None)
    monkeypatch.setattr(session_module, "_sessionmaker", None)
    monkeypatch.setattr(session_module, "create_engine",
                        lambda url=None, pooled=True: create_async_engine(pg_url, poolclass=NullPool))


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from app.config import get_settings

    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Gap: one embedding space
# ---------------------------------------------------------------------------


class OtherEmbedder(HashingEmbedder):
    """Same vectors, different identity: stands in for switching to the RoBERTa model."""

    def __init__(self):
        super().__init__()
        self.name = "sentence-transformers/all-distilroberta-v1"


@pytest.mark.db
async def test_reembed_covers_scraped_rows_and_only_stale_ones(db_session):
    await add_item(db_session, brand_name="Jollibee", brand_slug="jollibee", food_item="Yumburger", calories=280)
    await add_item(db_session, brand_name="Army Navy", brand_slug="army navy", food_item="Burrito", calories=640)
    new = OtherEmbedder()
    assert await count_stale_embeddings(db_session, new.name) == 2  # add_item leaves model NULL

    assert await reembed_catalog(db_session, new) == 2
    assert await count_stale_embeddings(db_session, new.name) == 0
    assert await reembed_catalog(db_session, new) == 0  # nothing stale any more
    assert await reembed_catalog(db_session, new, only_stale=False) == 2
    models = (await db_session.execute(select(NutritionCatalog.embedding_model))).scalars().all()
    assert set(models) == {new.name}


# ---------------------------------------------------------------------------
# Gap: demo data toggles + purge
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_demo_items_toggle_and_purge_keep_real_rows(db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    emb = HashingEmbedder()
    off = await seed_module.run_seed(embedder=emb, demo_items=False, demo_branches=False)
    assert off["catalog_rows_written"] == 0 and off["branch_rows_inserted"] == 0

    on = await seed_module.run_seed(embedder=emb, demo_items=True, demo_branches=True)
    assert on["catalog_rows_written"] == len(seed_module.SEED_ITEMS)
    assert on["branch_rows_inserted"] == len(seed_module.SEED_BRANCHES)

    # A real scraped row that happens to share a demo key, and a verified edit of another demo row.
    first, second = seed_module.SEED_ITEMS[0], seed_module.SEED_ITEMS[1]
    await db_session.execute(
        NutritionCatalog.__table__.update()
        .where(NutritionCatalog.food_item == first.food_item)
        .values(source_post_url="https://www.instagram.com/p/REAL/", calories=999)
    )
    await db_session.execute(
        NutritionCatalog.__table__.update().where(NutritionCatalog.food_item == second.food_item)
        .values(is_verified=True)
    )
    await db_session.commit()

    again = await seed_module.run_seed(embedder=emb, demo_items=True, demo_branches=False)
    assert again["catalog_rows_written"] == 0  # real/verified rows are not clobbered by demo data
    purged = await seed_module.run_seed(embedder=emb, purge_demo=True, demo_branches=False)
    assert purged["demo_items_purged"] == len(seed_module.SEED_ITEMS) - 2

    left = {r.food_item: r for r in (await db_session.execute(select(NutritionCatalog))).scalars()}
    assert set(left) == {first.food_item, second.food_item}
    await db_session.refresh(left[first.food_item])
    assert left[first.food_item].calories == 999


@pytest.mark.db
async def test_seed_reembeds_demo_rows_when_model_changes(db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    await seed_module.run_seed(embedder=HashingEmbedder(), demo_items=True, demo_branches=False)
    result = await seed_module.run_seed(embedder=OtherEmbedder(), demo_items=True, demo_branches=False)
    assert result["catalog_rows_written"] == len(seed_module.SEED_ITEMS)


# ---------------------------------------------------------------------------
# Gap: brand list refresh + branch re-matching
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_rematch_is_conservative(db_session):
    await add_branch(db_session, osm_id=1, poi_name="Army Navy Taft", lat=14.57, lng=120.98, slug=None)
    # matched via the OSM brand tag earlier; the name alone wouldn't match - must be kept
    await add_branch(db_session, osm_id=2, poi_name="Food Court Stall 7", lat=14.57, lng=120.98, slug="jollibee")
    await add_branch(db_session, osm_id=3, poi_name="Gone Brand Cafe", lat=14.57, lng=120.98, slug="gone brand")
    result = await rematch_branches(db_session, BrandMatcher([*SEED_SLUGS, "army navy"]))
    assert result == {"checked": 3, "newly_matched": 1, "cleared": 1}
    slugs = dict((await db_session.execute(select(OsmBranchCache.osm_id, OsmBranchCache.matched_brand_slug))).all())
    assert slugs == {1: "army navy", 2: "jollibee", 3: None}


async def test_brand_registry_refreshes_when_stale():
    registry = BrandRegistry(BrandMatcher(["jollibee"]), max_age_s=300)
    calls = []

    async def fake_refresh(session):
        calls.append(session)

    registry.refresh = fake_refresh
    await registry.refresh_if_stale("s")
    assert calls == []  # fresh
    registry._loaded_at -= 301
    await registry.refresh_if_stale("s")
    assert calls == ["s"]


def test_local_status(dirs):
    _add_post(dirs, "SC1", "#jollibee", ["Yumburger 280 kcal"])
    _add_post(dirs, "SC2", "#jollibee", None)
    run_parse(dirs)
    status = local_status(dirs)
    assert status["posts_scraped"] == 2 and status["images"] == 2 and status["images_with_ocr"] == 1
    assert status["reviewed_load"] == 1 and status["posts_not_in_review"] == 0
