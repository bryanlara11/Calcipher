"""Catalog maintenance: re-embedding and brand re-matching of cached branches."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NutritionCatalog, OsmBranchCache
from app.services.brand_matcher import BrandMatcher
from app.services.embedder import Embedder
from app.services.ingestion_ocr import build_context_string

logger = logging.getLogger(__name__)


def _stale_filter(embedder_name: str):
    return or_(
        NutritionCatalog.embedding.is_(None),
        NutritionCatalog.embedding_model.is_(None),
        NutritionCatalog.embedding_model != embedder_name,
    )


async def count_stale_embeddings(session: AsyncSession, embedder_name: str) -> int:
    stmt = select(func.count()).select_from(NutritionCatalog).where(_stale_filter(embedder_name))
    return (await session.execute(stmt)).scalar_one()


async def reembed_catalog(
    session: AsyncSession, embedder: Embedder, *, only_stale: bool = True, batch_size: int = 64
) -> int:
    """Recompute context strings and embeddings for every catalog row (or only stale ones).

    Stale = no embedding, or produced by a different embedder than the one running now.
    """
    stmt = select(
        NutritionCatalog.id, NutritionCatalog.brand_name, NutritionCatalog.food_item,
        NutritionCatalog.serving_size, NutritionCatalog.calories, NutritionCatalog.protein_g,
        NutritionCatalog.carbs_g, NutritionCatalog.fat_g,
    ).order_by(NutritionCatalog.id)
    if only_stale:
        stmt = stmt.where(_stale_filter(embedder.name))
    rows = (await session.execute(stmt)).all()

    done = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        contexts = [
            build_context_string(r.brand_name, r.food_item, r.serving_size, r.calories,
                                 r.protein_g, r.carbs_g, r.fat_g)
            for r in batch
        ]
        vectors = await asyncio.to_thread(embedder.embed, contexts)
        await session.execute(
            update(NutritionCatalog),
            [
                {"id": r.id, "context_string": ctx, "embedding": vec, "embedding_model": embedder.name}
                for r, ctx, vec in zip(batch, contexts, vectors, strict=True)
            ],
        )
        await session.commit()
        done += len(batch)
        logger.info("re-embedded %d/%d rows", done, len(rows))
    return done


async def rematch_branches(session: AsyncSession, matcher: BrandMatcher) -> dict[str, int]:
    """Re-run brand matching over cached OSM branches after the catalog's brand list changed.

    Conservative: an unmatched branch may gain a slug, and a slug whose brand no longer exists
    in the catalog is cleared, but a still-valid slug is never replaced. (The cache does not
    store the OSM `brand` tag, so a match that came from that tag can't be re-derived from
    the name alone.)
    """
    rows = (
        await session.execute(
            select(OsmBranchCache.osm_type, OsmBranchCache.osm_id, OsmBranchCache.poi_name,
                   OsmBranchCache.matched_brand_slug)
        )
    ).all()
    changes = []
    newly_matched = cleared = 0
    for r in rows:
        current = r.matched_brand_slug
        if current is not None and current in matcher.slugs:
            continue
        hit = matcher.match(r.poi_name)
        new = hit.slug if hit else None
        if new != current:
            changes.append({"osm_type": r.osm_type, "osm_id": r.osm_id, "matched_brand_slug": new})
            if new is None:
                cleared += 1
            else:
                newly_matched += 1
    if changes:
        await session.execute(update(OsmBranchCache), changes)
        await session.commit()
    return {"checked": len(rows), "newly_matched": newly_matched, "cleared": cleared}
