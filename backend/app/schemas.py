"""Pydantic v2 request / response models and the structured SearchConstraints contract."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import PH_LAT_MAX, PH_LAT_MIN, PH_LNG_MAX, PH_LNG_MIN

MAX_QUERY_LENGTH = 500
DEFAULT_DISTANCE_KM = 3.0
RELAXED_DISTANCE_KM = 5.0
MAX_DISTANCE_KM = 10.0

Latitude = Annotated[float, Field(ge=PH_LAT_MIN, le=PH_LAT_MAX, description="WGS84 latitude in the PH")]
Longitude = Annotated[float, Field(ge=PH_LNG_MIN, le=PH_LNG_MAX, description="WGS84 longitude in the PH")]


class SearchConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_calories: int | None = Field(None, ge=0, le=10_000)
    max_price_php: float | None = Field(None, ge=0, le=99999999.99, allow_inf_nan=False)
    min_protein_g: float | None = Field(None, ge=0, le=500)
    max_distance_km: float = Field(DEFAULT_DISTANCE_KM, gt=0, le=MAX_DISTANCE_KM)
    target_brand: str | None = Field(None, max_length=255)
    semantic_query: str = Field("", max_length=MAX_QUERY_LENGTH)


# ---------- requests ----------


class MealFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_calories: int | None = Field(None, ge=0, le=10_000)
    min_protein_g: float | None = Field(None, ge=0, le=500, allow_inf_nan=False)
    max_price_php: float | None = Field(None, ge=0, le=99999999.99, allow_inf_nan=False)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    lat: Latitude
    lng: Longitude
    filters: MealFilters | None = None
    radius_km: float | None = Field(
        None, gt=0, le=MAX_DISTANCE_KM, description="Default search radius if the message names none"
    )


# ---------- shared ----------


class SourceEvidence(BaseModel):
    rating_updated_at: datetime | None = None
    rating_average: float | None = None
    rating_count: int = 0
    community_pick: bool = False
    source_classification: Literal["official", "community_reported", "estimated", "unknown"] = "unknown"
    collected_at: date | None = None
    secondary_source_url: str | None = None
    nutrition_source_url: str | None = None


class RetrievedItem(SourceEvidence):
    source_images: list[str] = Field(default_factory=list)
    item_id: str
    brand_name: str
    brand_slug: str
    food_item: str
    calories: int
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    serving_size: str | None
    is_verified: bool
    source_post_url: str | None
    price_php: float | None = None
    price_source_url: str | None = None
    place_id: str | None = None
    attributions: list[dict[str, str]] = Field(default_factory=list)
    osm_type: str | None = None
    osm_id: int | None = None
    poi_name: str
    amenity_type: str | None
    latitude: float
    longitude: float
    address_hint: str | None
    distance_km: float
    cosine_distance: float | None


class DiscoveryTrace(BaseModel):
    provider: Literal["google", "osm"] | None = None
    source: Literal["cache", "live", "stale_cache_fallback", "disabled"]
    cache_hit: bool
    fetched_count: int = 0
    matched_count: int = 0
    purged_seed_rows: int = 0
    radius_m: int
    error: str | None = None
    elapsed_ms: float


class RelaxationInfo(BaseModel):
    applied: bool = False
    steps: list[str] = Field(default_factory=list)
    notice: str | None = None
    final_constraints: SearchConstraints | None = None


class InspectorTrace(BaseModel):
    request_id: str | None = None
    sanitized_query: str
    injection_flags: list[str]
    parser_used: Literal["llm", "regex", "llm+regex"]
    parser_warnings: list[str]
    constraints: SearchConstraints
    discovery: list[DiscoveryTrace]
    embedder: str
    candidate_count: int
    catalog_candidate_count: int = 0
    retrieval_attempts: int
    generator_used: Literal["llm", "template"]
    timings_ms: dict[str, float]


# ---------- responses ----------


class ChatResponse(BaseModel):
    answer: str
    results: list[RetrievedItem]
    catalog_matches: list[RetrievedItem] = Field(default_factory=list)
    relaxation: RelaxationInfo
    inspector: InspectorTrace


class BranchPin(BaseModel):
    place_id: str | None = None
    attributions: list[dict[str, str]] = Field(default_factory=list)
    osm_type: str | None = None
    osm_id: int | None = None
    poi_name: str
    amenity_type: str | None
    latitude: float
    longitude: float
    address_hint: str | None
    distance_km: float
    matched_brand_slug: str | None
    brand_name: str | None
    has_calorie_data: bool
    has_catalog_data: bool = False
    catalog_item_count: int = 0
    pending_item_count: int = 0
    item_count: int
    min_calories: int | None
    max_protein_g: float | None


class MapItemsResponse(BaseModel):
    center: dict[str, float]
    radius_km: float
    branches: list[BranchPin]
    matched_count: int
    unmatched_count: int
    discovery: DiscoveryTrace


class CatalogItem(SourceEvidence):
    source_images: list[str] = Field(default_factory=list)
    item_id: str
    brand_name: str
    food_item: str
    calories: int
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    serving_size: str | None
    is_verified: bool
    source_post_url: str | None
    price_php: float | None = None
    price_source_url: str | None = None


class BrandItemsResponse(BaseModel):
    brand_slug: str
    items: list[CatalogItem]


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str | None = None


class MealRatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voter_id: UUID
    score: int = Field(ge=1, le=5, strict=True)
