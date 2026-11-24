"""SQLAlchemy 2.0 ORM models.

`nutrition_catalog` is location-agnostic (scraped from @anongcaloriesnito). `osm_branch_cache`
is the spatial layer from Overpass. They are joined on
`osm_branch_cache.matched_brand_slug = nutrition_catalog.brand_slug`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 768

# Philippine bounding box enforced on every cached branch.
PH_LAT_MIN, PH_LAT_MAX = 4.5, 21.5
PH_LNG_MIN, PH_LNG_MAX = 116.0, 127.0

OSM_TYPES = ("node", "way", "relation", "seed")


class Base(DeclarativeBase):
    pass


class NutritionCatalog(Base):
    __tablename__ = "nutrition_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    food_item: Mapped[str] = mapped_column(String(255), nullable=False)
    calories: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    carbs_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    price_php: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_source_url: Mapped[str | None] = mapped_column(Text)
    source_images: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    serving_size: Mapped[str] = mapped_column(String(100), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source_post_url: Mapped[str | None] = mapped_column(Text)
    context_string: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # Which embedder produced `embedding` (e.g. "hash-768" or a sentence-transformers model id).
    # Rows whose value differs from the running embedder live in a different vector space.
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("calories >= 0 AND calories <= 10000", name="ck_catalog_calories_range"),
        CheckConstraint("protein_g IS NULL OR protein_g >= 0", name="ck_catalog_protein_nonneg"),
        CheckConstraint("carbs_g IS NULL OR carbs_g >= 0", name="ck_catalog_carbs_nonneg"),
        CheckConstraint("fat_g IS NULL OR fat_g >= 0", name="ck_catalog_fat_nonneg"),
        CheckConstraint("brand_slug <> ''", name="ck_catalog_slug_nonempty"),
        CheckConstraint("price_php IS NULL OR (price_php >= 0 AND price_source_url IS NOT NULL AND is_verified)",
                        name="ck_catalog_price_source"),
        UniqueConstraint("brand_slug", "food_item", "serving_size", name="uq_catalog_item"),
        Index(
            "ix_nutrition_catalog_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class OsmBranchCache(Base):
    __tablename__ = "osm_branch_cache"

    # OSM node/way/relation IDs are independent number spaces, so the type is part of the key.
    osm_type: Mapped[str] = mapped_column(String(10), nullable=False)
    osm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    poi_name: Mapped[str] = mapped_column(String(255), nullable=False)
    matched_brand_slug: Mapped[str | None] = mapped_column(String(255), index=True)
    amenity_type: Mapped[str | None] = mapped_column(String(50))
    latitude: Mapped[float] = mapped_column(Double, nullable=False)
    longitude: Mapped[float] = mapped_column(Double, nullable=False)
    address_hint: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("osm_type", "osm_id", name="pk_osm_branch_cache"),
        CheckConstraint(
            f"latitude BETWEEN {PH_LAT_MIN} AND {PH_LAT_MAX}", name="ck_branch_latitude_ph"
        ),
        CheckConstraint(
            f"longitude BETWEEN {PH_LNG_MIN} AND {PH_LNG_MAX}", name="ck_branch_longitude_ph"
        ),
        CheckConstraint(
            "osm_type IN ('node', 'way', 'relation', 'seed')", name="ck_branch_osm_type"
        ),
        Index("ix_osm_branch_cache_lat_lng", "latitude", "longitude"),
    )


class OverpassFetchLog(Base):
    """One row per successful Overpass fetch; drives the 24h spatial grid cache."""

    __tablename__ = "overpass_fetch_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    grid_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    center_lat: Mapped[float] = mapped_column(Double, nullable=False)
    center_lng: Mapped[float] = mapped_column(Double, nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False)
    element_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        CheckConstraint("radius_m > 0", name="ck_fetch_log_radius_pos"),
        CheckConstraint("element_count >= 0", name="ck_fetch_log_count_nonneg"),
    )
