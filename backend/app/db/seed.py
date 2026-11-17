"""Idempotent seed: nutrition catalog + baseline Metro Manila branches.

    python -m app.db.seed            # upsert catalog + branches (embeds only new/changed rows)
    python -m app.db.seed --reembed  # recompute EVERY catalog embedding (demo + scraped rows)
    python -m app.db.seed --purge-demo-items   # delete the illustrative demo items

SEED_DEMO_ITEMS / SEED_DEMO_BRANCHES (env) switch each part off; init_db always runs.

DATA HONESTY NOTE
- Nutrition values below are *illustrative estimates* written in the @anongcaloriesnito style for
  offline development. They were NOT scraped and are stored with `is_verified = false` and no
  `source_post_url`. Replace them with OCR-ingested rows (`app.services.ingestion_ocr`).
- Demo items are only written while SEED_DEMO_ITEMS=true; `--purge-demo-items` removes them
  (and never touches scraped or verified rows).
- Branch coordinates are *approximate* hand-placed points around UP Manila/Taft, Makati and
  Quezon City, stored with `osm_type = 'seed'` (not real OSM IDs). The first successful live
  Overpass fetch covering an area purges the seed rows inside it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.maintenance import reembed_catalog
from app.db.session import ensure_selector_event_loop_policy, get_engine, get_sessionmaker, init_db
from app.models import NutritionCatalog, OsmBranchCache, OverpassFetchLog
from app.services.brand_matcher import BrandMatcher
from app.services.embedder import Embedder, get_embedder
from app.services.ingestion_ocr import build_context_string
from app.services.overpass_client import circle_contains

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedItem:
    brand_name: str
    brand_slug: str
    food_item: str
    serving_size: str
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float


# Macros are kept Atwater-consistent (4P + 4C + 9F within ~5% of calories).
SEED_ITEMS: list[SeedItem] = [
    # Andok's
    SeedItem("Andok's", "andoks", "Litson Manok Quarter (Pecho)", "1/4 chicken, breast (~200g)", 390, 42, 2, 24),
    SeedItem("Andok's", "andoks", "Dokito Burger", "1 sandwich (165g)", 410, 16, 42, 20),
    SeedItem("Andok's", "andoks", "Liempo with Rice", "1 serving liempo + 1 cup rice", 820, 28, 48, 58),
    # 24 Chicken
    SeedItem("24 Chicken", "24 chicken", "Original Fried Chicken (2 pcs)", "2 pcs (~240g)", 560, 38, 18, 37),
    SeedItem("24 Chicken", "24 chicken", "Honey Garlic Chicken Rice Meal", "1 pc chicken + 1 cup rice", 680, 30, 78, 27),
    SeedItem("24 Chicken", "24 chicken", "Spicy Fried Chicken Thigh", "1 pc (~120g)", 330, 20, 9, 24),
    # Mang Inasal
    SeedItem("Mang Inasal", "mang inasal", "Chicken Inasal Pecho without Rice", "1 breast quarter (~230g)", 360, 45, 3, 18.5),
    SeedItem("Mang Inasal", "mang inasal", "Chicken Inasal Paa with Rice", "1 leg quarter + 1 cup rice", 620, 34, 48, 32),
    SeedItem("Mang Inasal", "mang inasal", "Pork BBQ with Rice", "2 sticks + 1 cup rice", 590, 26, 62, 26),
    SeedItem("Mang Inasal", "mang inasal", "Palabok", "1 regular serving", 430, 14, 58, 16),
    # Jollibee
    SeedItem("Jollibee", "jollibee", "Chickenjoy with Rice", "1 pc chicken + 1 cup rice", 540, 26, 50, 26),
    SeedItem("Jollibee", "jollibee", "Yumburger", "1 sandwich (~105g)", 280, 12, 32, 11.5),
    SeedItem("Jollibee", "jollibee", "Jolly Spaghetti", "1 regular serving", 480, 15, 70, 15.5),
    SeedItem("Jollibee", "jollibee", "Burger Steak with Rice", "1 pc patty + 1 cup rice", 470, 18, 56, 19.5),
    # Chowking
    SeedItem("Chowking", "chowking", "Pork Chao Fan", "1 regular serving", 560, 16, 78, 20.5),
    SeedItem("Chowking", "chowking", "Steamed Pork Siomai", "4 pcs", 230, 12, 14, 14),
    SeedItem("Chowking", "chowking", "Beef Wonton Mami", "1 regular bowl", 420, 22, 54, 13),
    # Kenny Rogers Roasters
    SeedItem("Kenny Rogers Roasters", "kenny rogers roasters", "Quarter Roast Chicken Breast with Rice", "1/4 chicken + 1 cup rice", 590, 44, 46, 25),
    SeedItem("Kenny Rogers Roasters", "kenny rogers roasters", "Skinless Roast Chicken Breast Quarter", "1/4 chicken, no skin, no rice", 290, 46, 1, 11),
    # Subway
    SeedItem("Subway", "subway", "Roasted Chicken Sub", "6-inch on wheat", 320, 23, 45, 5),
    SeedItem("Subway", "subway", "Turkey Breast Sub", "6-inch on wheat", 280, 18, 46, 3.5),
    SeedItem("Subway", "subway", "Steak & Cheese Sub", "6-inch on wheat", 380, 25, 46, 10.5),
    # 7-Eleven
    SeedItem("7-Eleven", "7 eleven", "Siopao Asado", "1 pc (~110g)", 290, 10, 46, 7.5),
    SeedItem("7-Eleven", "7 eleven", "Hard-Boiled Eggs", "2 pcs", 155, 12.5, 1, 10.5),
    SeedItem("7-Eleven", "7 eleven", "Chicken Adobo Rice Meal", "1 microwavable meal", 520, 24, 66, 17.5),
    # Uncle John's (Ministop)
    SeedItem("Uncle John's", "uncle johns", "Fried Chicken with Rice", "1 pc chicken + 1 cup rice", 560, 24, 52, 28.5),
    SeedItem("Uncle John's", "uncle johns", "Chicken Teriyaki Rice Bowl", "1 bowl", 510, 26, 70, 13.5),
]


@dataclass(frozen=True, slots=True)
class SeedBranch:
    seed_id: int
    poi_name: str
    amenity_type: str
    latitude: float
    longitude: float
    address_hint: str


# Approximate locations. Two unmatched brands (Starbucks, McDonald's) demonstrate grey pins.
SEED_BRANCHES: list[SeedBranch] = [
    # UP Manila / Taft / Ermita / Malate
    SeedBranch(1, "Jollibee Taft - Pedro Gil", "fast_food", 14.57680, 120.98840, "Taft Avenue, Manila"),
    SeedBranch(2, "Mang Inasal - Robinsons Place Manila", "fast_food", 14.57620, 120.98300, "Pedro Gil Street, Manila"),
    SeedBranch(3, "Chowking UN Avenue", "fast_food", 14.58240, 120.98500, "United Nations Avenue, Manila"),
    SeedBranch(4, "Andok's - Pedro Gil", "fast_food", 14.57700, 120.99020, "Pedro Gil Street, Manila"),
    SeedBranch(5, "24 Chicken Leon Guinto", "restaurant", 14.57300, 120.99050, "Leon Guinto Street, Manila"),
    SeedBranch(6, "7-Eleven Padre Faura", "fast_food", 14.57950, 120.98500, "Padre Faura Street, Manila"),
    SeedBranch(7, "Uncle John's - Taft Avenue", "fast_food", 14.57350, 120.98930, "Taft Avenue, Manila"),
    SeedBranch(8, "Subway Robinsons Place Manila", "fast_food", 14.57600, 120.98330, "Adriatico Street, Manila"),
    SeedBranch(9, "Kenny Rogers Roasters (Robinsons Manila)", "restaurant", 14.57580, 120.98280, "Adriatico Street, Manila"),
    SeedBranch(10, "Starbucks Padre Faura", "cafe", 14.57920, 120.98620, "Padre Faura Street, Manila"),
    SeedBranch(11, "McDonald's UN Avenue", "fast_food", 14.58200, 120.98620, "United Nations Avenue, Manila"),
    # Makati
    SeedBranch(21, "Jollibee Ayala Avenue", "fast_food", 14.55680, 121.02330, "Ayala Avenue, Makati"),
    SeedBranch(22, "Subway Makati Avenue", "fast_food", 14.55900, 121.02750, "Makati Avenue, Makati"),
    SeedBranch(23, "Mang Inasal Glorietta", "fast_food", 14.55120, 121.02500, "Ayala Center, Makati"),
    SeedBranch(24, "Kenny Rogers Greenbelt", "restaurant", 14.55270, 121.02130, "Greenbelt, Makati"),
    SeedBranch(25, "Andok's Litson Manok - Makati Ave", "fast_food", 14.56500, 121.03000, "Makati Avenue, Makati"),
    SeedBranch(26, "7-Eleven Paseo de Roxas", "fast_food", 14.55600, 121.01800, "Paseo de Roxas, Makati"),
    SeedBranch(27, "Chowking Glorietta", "fast_food", 14.55080, 121.02580, "Ayala Center, Makati"),
    # Quezon City (UP Diliman / Katipunan / Maginhawa)
    SeedBranch(41, "Jollibee Katipunan", "fast_food", 14.63830, 121.07450, "Katipunan Avenue, Quezon City"),
    SeedBranch(42, "Mang Inasal Katipunan", "fast_food", 14.63700, 121.07400, "Katipunan Avenue, Quezon City"),
    SeedBranch(43, "24 Chicken Maginhawa", "restaurant", 14.64740, 121.05900, "Maginhawa Street, Quezon City"),
    SeedBranch(44, "Andok's Kalayaan Avenue", "fast_food", 14.65000, 121.04800, "Kalayaan Avenue, Quezon City"),
    SeedBranch(45, "7-Eleven UP Town Center", "fast_food", 14.65050, 121.07500, "Katipunan Avenue, Quezon City"),
    SeedBranch(46, "Subway UP Town Center", "fast_food", 14.65100, 121.07530, "Katipunan Avenue, Quezon City"),
    SeedBranch(47, "Uncle John's Katipunan", "fast_food", 14.63300, 121.07350, "Katipunan Avenue, Quezon City"),
]


async def seed_catalog(session: AsyncSession, embedder: Embedder, *, reembed: bool = False) -> int:
    existing = {
        (r.brand_slug, r.food_item, r.serving_size): r
        for r in (await session.execute(select(NutritionCatalog))).scalars()
    }
    pending: list[tuple[SeedItem, str]] = []
    for item in SEED_ITEMS:
        ctx = build_context_string(
            item.brand_name, item.food_item, item.serving_size, item.calories,
            item.protein_g, item.carbs_g, item.fat_g,
        )
        row = existing.get((item.brand_slug, item.food_item, item.serving_size))
        if row is not None and (row.source_post_url is not None or row.is_verified):
            continue  # a real (scraped or audited) row with the same key wins over demo data
        if (
            reembed
            or row is None
            or row.context_string != ctx
            or row.embedding is None
            or row.embedding_model != embedder.name
        ):
            pending.append((item, ctx))
    if not pending:
        return 0

    vectors = await asyncio.to_thread(embedder.embed, [ctx for _, ctx in pending])
    rows = [
        {
            "brand_name": it.brand_name,
            "brand_slug": it.brand_slug,
            "food_item": it.food_item,
            "calories": it.calories,
            "protein_g": it.protein_g,
            "carbs_g": it.carbs_g,
            "fat_g": it.fat_g,
            "serving_size": it.serving_size,
            "is_verified": False,
            "source_post_url": None,
            "context_string": ctx,
            "embedding": vec,
            "embedding_model": embedder.name,
        }
        for (it, ctx), vec in zip(pending, vectors, strict=True)
    ]
    stmt = insert(NutritionCatalog).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_catalog_item",
        set_={c: stmt.excluded[c] for c in ("brand_name", "calories", "protein_g", "carbs_g",
                                              "fat_g", "context_string", "embedding", "embedding_model")},
        where=and_(NutritionCatalog.source_post_url.is_(None), NutritionCatalog.is_verified.is_(False)),
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def seed_branches(session: AsyncSession) -> int:
    matcher = BrandMatcher({item.brand_slug for item in SEED_ITEMS})
    now = datetime.now(timezone.utc)
    # Never re-add approximate seed pins to an area that already received real Overpass data
    # (any age: live rows persist in the cache even after the 24h TTL expires).
    fetched = (
        await session.execute(
            select(OverpassFetchLog.center_lat, OverpassFetchLog.center_lng, OverpassFetchLog.radius_m)
        )
    ).all()
    rows = []
    for b in SEED_BRANCHES:
        if any(circle_contains(c_lat, c_lng, r, b.latitude, b.longitude, 0) for c_lat, c_lng, r in fetched):
            continue
        match = matcher.match(b.poi_name)
        rows.append(
            {
                "osm_type": "seed",
                "osm_id": b.seed_id,
                "poi_name": b.poi_name,
                "matched_brand_slug": match.slug if match else None,
                "amenity_type": b.amenity_type,
                "latitude": b.latitude,
                "longitude": b.longitude,
                "address_hint": b.address_hint,
                "last_fetched_at": now,
            }
        )
    if not rows:
        return 0
    stmt = insert(OsmBranchCache).values(rows)
    # DO NOTHING: re-seeding must not resurrect rows or reset timestamps of an existing cache.
    stmt = stmt.on_conflict_do_nothing(constraint="pk_osm_branch_cache").returning(OsmBranchCache.osm_id)
    inserted = len((await session.execute(stmt)).all())
    await session.commit()
    return inserted


async def purge_demo_items(session: AsyncSession) -> int:
    """Delete the illustrative demo items. Scraped (has a post URL) or verified rows are kept."""
    keys = [(i.brand_slug, i.food_item, i.serving_size) for i in SEED_ITEMS]
    stmt = (
        delete(NutritionCatalog)
        .where(
            tuple_(NutritionCatalog.brand_slug, NutritionCatalog.food_item, NutritionCatalog.serving_size).in_(keys),
            NutritionCatalog.source_post_url.is_(None),
            or_(NutritionCatalog.is_verified.is_(False), NutritionCatalog.is_verified.is_(None)),
        )
        .returning(NutritionCatalog.id)
    )
    deleted = len((await session.execute(stmt)).all())
    await session.commit()
    return deleted


async def run_seed(
    *,
    reembed: bool = False,
    purge_demo: bool = False,
    embedder: Embedder | None = None,
    demo_items: bool | None = None,
    demo_branches: bool | None = None,
) -> dict[str, int]:
    settings = get_settings()
    demo_items = settings.seed_demo_items if demo_items is None else demo_items
    demo_branches = settings.seed_demo_branches if demo_branches is None else demo_branches
    embedder = embedder or get_embedder()
    result = {"catalog_rows_written": 0, "branch_rows_inserted": 0, "demo_items_purged": 0, "rows_reembedded": 0}
    await init_db(get_engine())
    async with get_sessionmaker()() as session:
        if purge_demo:
            result["demo_items_purged"] = await purge_demo_items(session)
        elif demo_items:
            result["catalog_rows_written"] = await seed_catalog(session, embedder)
        if demo_branches:
            result["branch_rows_inserted"] = await seed_branches(session)
        if reembed:
            result["rows_reembedded"] = await reembed_catalog(session, embedder, only_stale=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed CalCipher demo data")
    parser.add_argument("--reembed", action="store_true",
                        help="recompute embeddings for every catalog row (demo and scraped)")
    parser.add_argument("--purge-demo-items", action="store_true",
                        help="delete the illustrative demo items instead of seeding them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_selector_event_loop_policy()
    print(asyncio.run(run_seed(reembed=args.reembed, purge_demo=args.purge_demo_items)))


if __name__ == "__main__":
    main()
