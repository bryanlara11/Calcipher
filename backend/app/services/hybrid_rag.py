"""Stages 3 + 4 — spatial join, hard numeric SQL filters, pgvector ranking, relaxation, generation.

All SQL lives in module-level constants with bound parameters only. User text never reaches SQL
except as the *value* of a bound parameter (and the semantic query only as an embedding vector).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import (
    RELAXED_DISTANCE_KM,
    BranchPin,
    CatalogItem,
    ChatResponse,
    DiscoveryTrace,
    InspectorTrace,
    RelaxationInfo,
    RetrievedItem,
    SearchConstraints,
)
from app.security import sanitize_user_text, validate_macro_physics
from app.services.brand_matcher import BrandRegistry
from app.services.embedder import Embedder, to_pgvector_literal
from app.services.generator import GroundedGenerator
from app.services.overpass_client import EARTH_RADIUS_KM, BranchDiscoveryService, DiscoveryResult, bounding_box
from app.services.google_places import GooglePlacesDiscovery
from app.services.query_parser import QueryParser

TOP_K = 5
CALORIE_RELAX_FACTOR = 1.15
MAP_PIN_LIMIT = 500
DEFAULT_SEMANTIC_QUERY = "filling meal"

# Haversine great-circle distance in km between the bound user point and a branch row `b`.
_HAVERSINE_KM = f"""(
    2 * {EARTH_RADIUS_KM} * asin(least(1.0, sqrt(
        power(sin(radians(b.latitude - CAST(:user_lat AS double precision)) / 2), 2)
        + cos(radians(CAST(:user_lat AS double precision))) * cos(radians(b.latitude))
        * power(sin(radians(b.longitude - CAST(:user_lng AS double precision)) / 2), 2)
    )))
)"""

_PLACES_FROM = """jsonb_to_recordset(CAST(:places AS jsonb)) AS b(
    place_id text, attributions jsonb, osm_type text, osm_id bigint, poi_name text, matched_brand_slug text,
    amenity_type text, latitude double precision, longitude double precision, address_hint text
)"""
_LEGACY_FROM = "(SELECT *, NULL::text AS place_id, NULL::jsonb AS attributions FROM osm_branch_cache) AS b"

_RETRIEVAL_QUERY = (
    f"""
WITH nearby AS (
    SELECT b.place_id, b.attributions, b.osm_type, b.osm_id, b.poi_name, b.matched_brand_slug, b.amenity_type,
           b.latitude, b.longitude, b.address_hint,
           {_HAVERSINE_KM} AS distance_km
    FROM {{branches}}
    WHERE b.matched_brand_slug IS NOT NULL
      AND b.latitude BETWEEN :min_lat AND :max_lat
      AND b.longitude BETWEEN :min_lng AND :max_lng
),
candidates AS (
    SELECT DISTINCT ON (n.id)
           n.id AS item_id, n.brand_name, n.brand_slug, n.food_item, n.calories,
           n.protein_g, n.carbs_g, n.fat_g, n.serving_size, n.is_verified, n.source_post_url,
           n.price_php, n.price_source_url, n.source_images,
           nb.place_id, nb.attributions, nb.osm_type, nb.osm_id, nb.poi_name, nb.amenity_type, nb.latitude, nb.longitude,
           nb.address_hint, nb.distance_km,
           (n.embedding <=> CAST(:query_vec AS vector)) AS cosine_distance
    FROM nutrition_catalog AS n
    JOIN nearby AS nb ON nb.matched_brand_slug = n.brand_slug
    WHERE nb.distance_km <= :max_distance_km
      AND (CAST(:max_cal AS integer) IS NULL OR n.calories <= CAST(:max_cal AS integer))
      AND (CAST(:min_protein AS numeric) IS NULL OR n.protein_g >= CAST(:min_protein AS numeric))
      AND (CAST(:max_price AS numeric) IS NULL OR (n.price_php IS NOT NULL AND n.is_verified
           AND n.price_source_url IS NOT NULL AND n.price_php <= CAST(:max_price AS numeric)))
      AND (CAST(:target_brand AS text) IS NULL OR n.brand_slug = CAST(:target_brand AS text))
    ORDER BY n.id, nb.distance_km ASC
)
SELECT c.*, COUNT(*) OVER () AS candidate_count
FROM candidates AS c
ORDER BY c.cosine_distance ASC NULLS LAST, c.distance_km ASC, c.item_id
LIMIT :k
"""
)

_MAP_QUERY = (
    f"""
WITH nearby AS (
    SELECT b.place_id, b.attributions, b.osm_type, b.osm_id, b.poi_name, b.matched_brand_slug, b.amenity_type,
           b.latitude, b.longitude, b.address_hint,
           {_HAVERSINE_KM} AS distance_km
    FROM {{branches}}
    WHERE b.latitude BETWEEN :min_lat AND :max_lat
      AND b.longitude BETWEEN :min_lng AND :max_lng
),
catalog AS (
    SELECT brand_slug, MIN(brand_name) AS brand_name, COUNT(*) AS item_count,
           MIN(calories) AS min_calories, MAX(protein_g) AS max_protein_g
    FROM nutrition_catalog
    GROUP BY brand_slug
)
SELECT nb.*, c.brand_name, COALESCE(c.item_count, 0) AS item_count, c.min_calories,
       c.max_protein_g, (c.brand_slug IS NOT NULL) AS has_calorie_data
FROM nearby AS nb
LEFT JOIN catalog AS c ON c.brand_slug = nb.matched_brand_slug
WHERE nb.distance_km <= :radius_km
  AND (CAST(:include_unmatched AS boolean) OR c.brand_slug IS NOT NULL)
ORDER BY nb.distance_km ASC
LIMIT :limit
"""
)

RETRIEVAL_SQL = text(_RETRIEVAL_QUERY.format(branches=_LEGACY_FROM))
MAP_ITEMS_SQL = text(_MAP_QUERY.format(branches=_LEGACY_FROM))
PLACES_RETRIEVAL_SQL = text(_RETRIEVAL_QUERY.format(branches=_PLACES_FROM))
PLACES_MAP_SQL = text(_MAP_QUERY.format(branches=_PLACES_FROM))

BRAND_ITEMS_SQL = text(
    """
SELECT id AS item_id, brand_name, food_item, calories, protein_g, carbs_g, fat_g, serving_size,
       is_verified, source_post_url, price_php, price_source_url, source_images
FROM nutrition_catalog
WHERE brand_slug = :brand_slug
ORDER BY calories ASC
LIMIT 100
"""
)


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(slots=True)
class RetrievalResult:
    items: list[RetrievedItem]
    candidate_count: int


async def retrieve(
    session: AsyncSession,
    *,
    lat: float,
    lng: float,
    constraints: SearchConstraints,
    query_vec: Sequence[float],
    places: list[dict] | None = None,
    k: int = TOP_K,
) -> RetrievalResult:
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, constraints.max_distance_km)
    params = {
        "user_lat": lat,
        "user_lng": lng,
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lng": min_lng,
        "max_lng": max_lng,
        "max_distance_km": constraints.max_distance_km,
        "max_cal": constraints.max_calories,
        "max_price": constraints.max_price_php,
        "min_protein": constraints.min_protein_g,
        "target_brand": constraints.target_brand,
        "query_vec": to_pgvector_literal(list(query_vec)),
        "k": k,
    }
    query = RETRIEVAL_SQL
    if places is not None:
        query = PLACES_RETRIEVAL_SQL
        params["places"] = json.dumps(places)
    rows = (await session.execute(query, params)).mappings().all()
    items = [
        RetrievedItem(
            item_id=str(r["item_id"]),
            brand_name=r["brand_name"],
            brand_slug=r["brand_slug"],
            food_item=r["food_item"],
            calories=r["calories"],
            protein_g=_f(r["protein_g"]),
            carbs_g=_f(r["carbs_g"]),
            fat_g=_f(r["fat_g"]),
            serving_size=r["serving_size"],
            is_verified=r["is_verified"],
            source_post_url=r["source_post_url"],
            price_php=_f(r["price_php"]),
            price_source_url=r["price_source_url"],
            source_images=r["source_images"],
            place_id=r["place_id"],
            attributions=r["attributions"] or [],
            osm_type=r["osm_type"],
            osm_id=r["osm_id"],
            poi_name=r["poi_name"],
            amenity_type=r["amenity_type"],
            latitude=r["latitude"],
            longitude=r["longitude"],
            address_hint=r["address_hint"],
            distance_km=round(float(r["distance_km"]), 3),
            cosine_distance=None if r["cosine_distance"] is None else round(float(r["cosine_distance"]), 5),
        )
        for r in rows
    ]
    return RetrievalResult(items, int(rows[0]["candidate_count"]) if rows else 0)


async def nearby_branches(
    session: AsyncSession, *, lat: float, lng: float, radius_km: float, include_unmatched: bool,
    places: list[dict] | None = None,
) -> list[BranchPin]:
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_km)
    rows = (
        await session.execute(
            PLACES_MAP_SQL if places is not None else MAP_ITEMS_SQL,
            {
                "user_lat": lat,
                "user_lng": lng,
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "radius_km": radius_km,
                "include_unmatched": include_unmatched,
                "limit": MAP_PIN_LIMIT,
                "places": json.dumps(places),
            },
        )
    ).mappings().all()
    return [
        BranchPin(
            place_id=r["place_id"],
            attributions=r["attributions"] or [],
            osm_type=r["osm_type"],
            osm_id=r["osm_id"],
            poi_name=r["poi_name"],
            amenity_type=r["amenity_type"],
            latitude=r["latitude"],
            longitude=r["longitude"],
            address_hint=r["address_hint"],
            distance_km=round(float(r["distance_km"]), 3),
            matched_brand_slug=r["matched_brand_slug"],
            brand_name=r["brand_name"],
            has_calorie_data=bool(r["has_calorie_data"]),
            item_count=int(r["item_count"]),
            min_calories=r["min_calories"],
            max_protein_g=_f(r["max_protein_g"]),
        )
        for r in rows
    ]


async def brand_items(session: AsyncSession, brand_slug: str) -> list[CatalogItem]:
    rows = (await session.execute(BRAND_ITEMS_SQL, {"brand_slug": brand_slug})).mappings().all()
    return [
        CatalogItem(
            item_id=str(r["item_id"]),
            brand_name=r["brand_name"],
            food_item=r["food_item"],
            calories=r["calories"],
            protein_g=_f(r["protein_g"]),
            carbs_g=_f(r["carbs_g"]),
            fat_g=_f(r["fat_g"]),
            serving_size=r["serving_size"],
            is_verified=r["is_verified"],
            source_post_url=r["source_post_url"],
            price_php=_f(r["price_php"]),
            price_source_url=r["price_source_url"],
            source_images=r["source_images"],
        )
        for r in rows
    ]


def relaxation_ladder(c: SearchConstraints) -> list[tuple[str, SearchConstraints]]:
    """Ordered fallbacks for a zero-result query: calories +15% -> 5 km -> both."""
    steps: list[tuple[str, SearchConstraints]] = []
    relaxed_cal = math.ceil(c.max_calories * CALORIE_RELAX_FACTOR) if c.max_calories is not None else None
    can_widen = c.max_distance_km < RELAXED_DISTANCE_KM
    if relaxed_cal is not None:
        steps.append((
            f"calorie limit raised 15% from {c.max_calories} to {relaxed_cal} kcal",
            c.model_copy(update={"max_calories": relaxed_cal}),
        ))
    if can_widen:
        steps.append((
            f"search radius expanded from {c.max_distance_km:g} km to {RELAXED_DISTANCE_KM:g} km",
            c.model_copy(update={"max_distance_km": RELAXED_DISTANCE_KM}),
        ))
    if relaxed_cal is not None and can_widen:
        steps.append((
            f"calorie limit raised 15% to {relaxed_cal} kcal and radius expanded to {RELAXED_DISTANCE_KM:g} km",
            c.model_copy(update={"max_calories": relaxed_cal, "max_distance_km": RELAXED_DISTANCE_KM}),
        ))
    return steps


def _trace(d: DiscoveryResult) -> DiscoveryTrace:
    return DiscoveryTrace(**d.as_dict())


class HybridRagService:
    def __init__(
        self,
        *,
        discovery: BranchDiscoveryService | GooglePlacesDiscovery,
        parser: QueryParser,
        embedder: Embedder,
        generator: GroundedGenerator,
        brands: BrandRegistry,
    ) -> None:
        self.discovery = discovery
        self.parser = parser
        self.embedder = embedder
        self.generator = generator
        self.brands = brands

    async def chat(
        self,
        session: AsyncSession,
        *,
        message: str,
        lat: float,
        lng: float,
        default_radius_km: float | None = None,
        request_id: str | None = None,
    ) -> ChatResponse:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        def lap(name: str, since: float) -> float:
            now = time.perf_counter()
            timings[name] = round((now - since) * 1000, 2)
            return now

        # Stage 2 first: parsing needs no I/O, so impossible requests are rejected (by
        # validate_macro_physics raising MacroPhysicsError) before Overpass or PostgreSQL.
        sanitized = sanitize_user_text(message)
        parse_kwargs = {"default_distance_km": default_radius_km} if default_radius_km else {}
        parsed = await self.parser.parse(sanitized.text, self.brands.matcher, **parse_kwargs)
        constraints = parsed.constraints
        validate_macro_physics(constraints)
        t = lap("parse", t0)

        # Stage 1: discovery (Overpass or cache).
        brand_filter = {"brand": constraints.target_brand} if isinstance(self.discovery, GooglePlacesDiscovery) else {}
        discoveries = [await self.discovery.ensure_branches(session, lat, lng, constraints.max_distance_km, **brand_filter)]
        if discoveries[0].source == "live" and discoveries[0].places is None:
            await self.brands.refresh(session)
        else:
            await self.brands.refresh_if_stale(session)
        t = lap("discovery", t)

        # Stage 3: embed + hybrid SQL.
        semantic = constraints.semantic_query or DEFAULT_SEMANTIC_QUERY
        query_vec = await asyncio.to_thread(self.embedder.embed_one, semantic)
        t = lap("embed", t)
        result = await retrieve(session, lat=lat, lng=lng, constraints=constraints, query_vec=query_vec,
                                places=discoveries[0].places)
        attempts = 1
        relaxation = RelaxationInfo()
        final = constraints

        # Stage 4a: zero-result relaxation.
        if not result.items:
            for note, relaxed in relaxation_ladder(constraints):
                if relaxed.max_distance_km > constraints.max_distance_km and len(discoveries) == 1:
                    discoveries.append(
                        await self.discovery.ensure_branches(session, lat, lng, relaxed.max_distance_km, **brand_filter)
                    )
                attempts += 1
                relaxation.steps.append(note)
                candidate = await retrieve(session, lat=lat, lng=lng, constraints=relaxed, query_vec=query_vec,
                                           places=discoveries[-1].places)
                if candidate.items:
                    result, final = candidate, relaxed
                    relaxation.applied = True
                    relaxation.notice = (
                        "No nearby mapped restaurant matched your exact request, so I relaxed it: "
                        f"{note}."
                    )
                    break
            relaxation.final_constraints = final
        t = lap("retrieval", t)

        # Stage 4b: grounded generation.
        answer, generator_used = await self.generator.generate(
            sanitized.text, result.items, final, relaxation.notice, original_constraints=constraints
        )
        lap("generation", t)
        timings["total"] = round((time.perf_counter() - t0) * 1000, 2)

        inspector = InspectorTrace(
            request_id=request_id or str(uuid.uuid4()),
            sanitized_query=sanitized.text,
            injection_flags=sanitized.flags,
            parser_used=parsed.parser_used,
            parser_warnings=parsed.warnings,
            constraints=constraints,
            discovery=[_trace(d) for d in discoveries],
            embedder=self.embedder.name,
            candidate_count=result.candidate_count,
            retrieval_attempts=attempts,
            generator_used=generator_used,
            timings_ms=timings,
        )
        return ChatResponse(answer=answer, results=result.items, relaxation=relaxation, inspector=inspector)

    async def map_items(
        self, session: AsyncSession, *, lat: float, lng: float, radius_km: float, include_unmatched: bool, viewport=None,
    ) -> tuple[list[BranchPin], DiscoveryTrace]:
        options = {"spread_map": True, "viewport": viewport} if isinstance(self.discovery, GooglePlacesDiscovery) else {}
        discovery = await self.discovery.ensure_branches(session, lat, lng, radius_km, **options)
        if discovery.source == "live" and discovery.places is None:
            await self.brands.refresh(session)
        else:
            await self.brands.refresh_if_stale(session)
        pins = await nearby_branches(
            session, lat=lat, lng=lng, radius_km=radius_km, include_unmatched=include_unmatched,
            places=discovery.places,
        )
        return pins, _trace(discovery)
