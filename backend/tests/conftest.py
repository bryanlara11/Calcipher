"""Shared fixtures.

Unit tests never touch the network or download models. Tests marked `db` need PostgreSQL with
pgvector: `TEST_DATABASE_URL` (e.g. the docker-compose db) if set, otherwise an embedded
`pgserver` instance (PG16 + pgvector) is started in a temp dir; if neither is possible they skip.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

# Must be set before app modules read settings.
os.environ["EMBEDDER_BACKEND"] = "hash"
os.environ["OVERPASS_ENABLED"] = "false"
os.environ["GEMINI_API_KEY"] = ""
os.environ["AUTO_CREATE_SCHEMA"] = "false"

if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db.seed import SEED_ITEMS  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.models import NutritionCatalog, OsmBranchCache  # noqa: E402
from app.services.brand_matcher import BrandMatcher  # noqa: E402
from app.services.embedder import HashingEmbedder, set_embedder  # noqa: E402
from app.services.ingestion_ocr import build_context_string  # noqa: E402
from app.services.overpass_client import EARTH_RADIUS_KM  # noqa: E402

SEED_SLUGS = sorted({item.brand_slug for item in SEED_ITEMS})

# UP Manila (Padre Faura / Taft) — the reference point for spatial tests.
UPM_LAT, UPM_LNG = 14.5785, 120.9850


def point_north(lat: float, lng: float, km: float) -> tuple[float, float]:
    """A point exactly `km` (great-circle) north of (lat, lng)."""
    return lat + math.degrees(km / EARTH_RADIUS_KM), lng


@pytest.fixture(scope="session", autouse=True)
def _hash_embedder() -> Iterator[None]:
    set_embedder(HashingEmbedder())
    yield
    set_embedder(None)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


@pytest.fixture
def matcher() -> BrandMatcher:
    return BrandMatcher(SEED_SLUGS)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("PostgreSQL unavailable: set TEST_DATABASE_URL or `pip install pgserver`")
    data_dir = tempfile.mkdtemp(prefix="calcipher-pg-")
    try:
        server = pgserver.get_server(data_dir, cleanup_mode="stop")
        server.psql("CREATE DATABASE calcipher_test;")
    except Exception as exc:  # pragma: no cover - environment specific
        shutil.rmtree(data_dir, ignore_errors=True)
        pytest.skip(f"embedded PostgreSQL failed to start: {exc}")
    uri = server.get_uri("calcipher_test")
    yield uri.replace("postgresql://", "postgresql+psycopg://", 1)
    server.cleanup()
    shutil.rmtree(data_dir, ignore_errors=True)


_SCHEMA_READY: dict[str, bool] = {}


@pytest.fixture
async def db_engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    if not _SCHEMA_READY:
        _SCHEMA_READY.update(await init_db(engine))
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE nutrition_catalog, osm_branch_cache, overpass_fetch_log"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def add_item(
    session: AsyncSession,
    *,
    brand_name: str,
    brand_slug: str,
    food_item: str,
    calories: int,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    serving_size: str = "1 serving",
    embedder: HashingEmbedder | None = None,
    verified: bool = True,
) -> None:
    ctx = build_context_string(brand_name, food_item, serving_size, calories, protein_g, carbs_g, fat_g)
    vec = (embedder or HashingEmbedder()).embed_one(ctx)
    await session.execute(
        insert(NutritionCatalog).values(
            brand_name=brand_name, brand_slug=brand_slug, food_item=food_item, calories=calories,
            protein_g=protein_g, carbs_g=carbs_g, fat_g=fat_g, serving_size=serving_size,
            context_string=ctx, embedding=vec, is_verified=verified,
        )
    )
    await session.commit()


async def add_branch(
    session: AsyncSession,
    *,
    osm_id: int,
    poi_name: str,
    lat: float,
    lng: float,
    slug: str | None,
    osm_type: str = "node",
    amenity: str = "fast_food",
    fetched_at: datetime | None = None,
) -> None:
    await session.execute(
        insert(OsmBranchCache).values(
            osm_type=osm_type, osm_id=osm_id, poi_name=poi_name, matched_brand_slug=slug,
            amenity_type=amenity, latitude=lat, longitude=lng, address_hint=None,
            last_fetched_at=fetched_at or datetime.now(timezone.utc),
        )
    )
    await session.commit()
