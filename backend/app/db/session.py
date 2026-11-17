"""Async engine / session management and idempotent schema initialisation."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def ensure_selector_event_loop_policy() -> None:
    """psycopg's async driver cannot run on Windows' default ProactorEventLoop."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def create_engine(database_url: str | None = None, *, pooled: bool = True) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    if pooled:
        return create_async_engine(
            url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
        )
    return create_async_engine(url, poolclass=NullPool)


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Creating a session does not open a connection until first use."""
    async with get_sessionmaker()() as session:
        yield session


async def init_db(engine: AsyncEngine) -> dict[str, bool]:
    """Create extensions, tables and indexes. Safe to run on every boot.

    pgvector is mandatory. pg_trgm is optional (the embedded test server lacks it); when present
    a trigram GIN index is added on `osm_branch_cache.poi_name`.
    """
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    has_trgm = True
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as exc:  # extension not installed on this server
        has_trgm = False
        logger.warning("pg_trgm unavailable, skipping trigram index: %s", exc.__class__.__name__)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if has_trgm:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_osm_branch_cache_poi_name_trgm "
                    "ON osm_branch_cache USING gin (poi_name gin_trgm_ops)"
                )
            )
    return {"pg_trgm": has_trgm}
