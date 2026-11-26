"""Stages 3 + 4 — spatial join, hard numeric SQL filters, pgvector ranking, relaxation, generation.

All SQL lives in module-level constants with bound parameters only. User text never reaches SQL
except as the *value* of a bound parameter (and the semantic query only as an embedding vector).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import (
    BranchPin,
    CatalogItem,
    ChatResponse,
    DiscoveryTrace,
    InspectorTrace,
    MealFilters,
    NutrientRanking,
    RelaxationInfo,
    RetrievedItem,
    SearchConstraints,
)
from app.security import sanitize_user_text, unescape_for_parsing, validate_macro_physics
from app.services.brand_matcher import BrandRegistry
from app.services.embedder import Embedder, to_pgvector_literal
from app.services.generator import GroundedGenerator
from app.services.overpass_client import EARTH_RADIUS_KM, BranchDiscoveryService, DiscoveryResult, bounding_box
from app.services.discovery_fallback import OsmFirstDiscovery
from app.services.google_places import GooglePlacesDiscovery
from app.services.query_parser import QueryParser
from app.services.food_matching import (
    catalog_exclusion, catalog_patterns, descriptor_terms, food_pattern, question_field, search_order, search_terms,
)
from app.services.meal_answers import direct_answer, help_answer, menu_answer, snack_answer, unstored_answer
from app.services.query_parser import mentions_location, unknown_place, unstored_nutrients

TOP_K = 5
MAP_PIN_LIMIT = 500
RANKED_LIMIT = 10          # chat cards for "highest protein ..." style questions
CHAT_MATCH_LIMIT = 200     # chat cards for plain food searches ("chicken"); Search shows every match
SUGGESTION_CANDIDATES = 20  # rows the optional LLM may choose a suggestion from
BRAND_NAME_SQL = text("SELECT MIN(brand_name) FROM nutrition_catalog WHERE brand_slug = :slug")
DEFAULT_SEMANTIC_QUERY = "filling meal"

Discovery = BranchDiscoveryService | GooglePlacesDiscovery | OsmFirstDiscovery

# ---- Shared item filtering and ranking (Search, Chat and the nearby Chat path) --------------
# `{n}` is a table-alias prefix filled in from the constants below ("n." or ""), never user text.
# Every value is a bound parameter. The ranked column is chosen by CASE on a bound name that
# SearchConstraints restricts to calories/protein_g/carbs_g/fat_g, so no column name is interpolated.
_CAL_UPPER = "COALESCE(CAST({n}provenance->'calorie_bounds'->>1 AS numeric), {n}calories)"
_CAL_LOWER = "COALESCE(CAST({n}provenance->'calorie_bounds'->>0 AS numeric), {n}calories)"
_SORT_VALUE = (
    "CASE CAST(:sort_by AS text) WHEN 'calories' THEN " + _CAL_UPPER
    + " WHEN 'protein_g' THEN {n}protein_g WHEN 'carbs_g' THEN {n}carbs_g WHEN 'fat_g' THEN {n}fat_g END"
)
_ITEM_FILTERS = (
    """(CAST(:target_brand AS text) IS NULL OR {n}brand_slug = CAST(:target_brand AS text))
      AND (CAST(:max_cal AS integer) IS NULL OR {n}calories <= CAST(:max_cal AS integer))
      AND (CAST(:min_cal AS integer) IS NULL OR """ + _CAL_LOWER + """ >= CAST(:min_cal AS integer))
      AND (CAST(:min_protein AS numeric) IS NULL OR {n}protein_g >= CAST(:min_protein AS numeric))
      AND (CAST(:max_protein AS numeric) IS NULL OR {n}protein_g <= CAST(:max_protein AS numeric))
      AND (CAST(:min_carbs AS numeric) IS NULL OR {n}carbs_g >= CAST(:min_carbs AS numeric))
      AND (CAST(:max_carbs AS numeric) IS NULL OR {n}carbs_g <= CAST(:max_carbs AS numeric))
      AND (CAST(:min_fat AS numeric) IS NULL OR {n}fat_g >= CAST(:min_fat AS numeric))
      AND (CAST(:max_fat AS numeric) IS NULL OR {n}fat_g <= CAST(:max_fat AS numeric))
      AND (CAST(:max_price AS numeric) IS NULL OR ({n}price_php IS NOT NULL AND {n}is_verified
           AND {n}price_source_url IS NOT NULL AND {n}price_php <= CAST(:max_price AS numeric)))
      AND (CAST(:sort_by AS text) IS NULL OR (""" + _SORT_VALUE + """) IS NOT NULL)
      AND (CAST(:exclude AS text) IS NULL
           OR translate(lower(concat_ws(', ', {n}food_item, {n}provenance->>'notes')), 'áéíóúñ', 'aeioun')
              !~ CAST(:exclude AS text))
      AND COALESCE((
          SELECT CASE WHEN CAST(:match_any AS boolean) THEN bool_or(hit) ELSE bool_and(hit) END
          FROM unnest(CAST(:patterns AS text[])) AS term(pattern)
          CROSS JOIN LATERAL (SELECT (
              translate(lower({n}food_item), 'áéíóúñ', 'aeioun') ~ pattern
              OR regexp_replace(translate(lower(concat_ws(', ', {n}serving_size, {n}provenance->>'notes')),
                                          'áéíóúñ', 'aeioun'), CAST(:note_noise AS text), '', 'g')
                 ~ ('\\m' || pattern)
          ) AS hit) AS matches
      ), TRUE)"""
)
# "Heavy snack" keeps the old preference for 300–600 kcal portions before plain calorie order.
_RANK_ORDER = """CASE WHEN CAST(:snack_window AS boolean) THEN
              CASE WHEN {v} < 300 THEN 300 - {v} WHEN {v} > 600 THEN {v} - 600 ELSE 0 END END ASC NULLS LAST,
         CASE WHEN CAST(:sort_dir AS text) = 'desc' THEN {v} END DESC NULLS LAST,
         CASE WHEN CAST(:sort_dir AS text) = 'asc' THEN {v} END ASC NULLS LAST"""

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
           n.id AS item_id, n.brand_name, n.brand_slug, n.food_item,
           COALESCE(CAST(n.provenance->'calorie_bounds'->>1 AS numeric), n.calories) AS calories,
           n.protein_g, n.carbs_g, n.fat_g, n.serving_size, n.is_verified, n.source_post_url,
           n.price_php, n.price_source_url, n.source_images, n.provenance,
           nb.place_id, nb.attributions, nb.osm_type, nb.osm_id, nb.poi_name, nb.amenity_type, nb.latitude, nb.longitude,
           nb.address_hint, nb.distance_km,
           (n.embedding <=> CAST(:query_vec AS vector)) AS cosine_distance,
           ({_SORT_VALUE.format(n="n.")}) AS sort_value
    FROM nutrition_catalog AS n
    JOIN nearby AS nb ON nb.matched_brand_slug = n.brand_slug
    WHERE n.is_active AND n.is_verified = CAST(:verified AS boolean)
      AND nb.distance_km <= :max_distance_km
      AND {_ITEM_FILTERS.format(n="n.")}
    ORDER BY n.id, nb.distance_km ASC
)
SELECT c.*, COUNT(*) OVER () AS candidate_count
FROM candidates AS c
ORDER BY {_RANK_ORDER.format(v="c.sort_value")},
         c.cosine_distance ASC NULLS LAST, c.distance_km ASC, c.item_id
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
    SELECT brand_slug, MIN(brand_name) AS brand_name,
           COUNT(*) FILTER (WHERE is_verified) AS item_count,
           COUNT(*) AS catalog_item_count,
           COUNT(*) FILTER (WHERE NOT is_verified) AS pending_item_count,
           MIN(COALESCE(CAST(provenance->'calorie_bounds'->>0 AS numeric), calories)) FILTER (WHERE is_verified) AS min_calories,
           MAX(protein_g) FILTER (WHERE is_verified) AS max_protein_g
    FROM nutrition_catalog
    WHERE is_active AND (is_verified OR CAST(:include_pending AS boolean))
    GROUP BY brand_slug
)
SELECT nb.*, c.brand_name, COALESCE(c.item_count, 0) AS item_count, c.min_calories,
       c.max_protein_g, (COALESCE(c.item_count, 0) > 0) AS has_calorie_data,
       (c.brand_slug IS NOT NULL) AS has_catalog_data,
       COALESCE(c.catalog_item_count, 0) AS catalog_item_count,
       COALESCE(c.pending_item_count, 0) AS pending_item_count
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

_CATALOG_COLUMNS = """
SELECT id AS item_id, brand_name, food_item,
       COALESCE(CAST(provenance->'calorie_bounds'->>1 AS numeric), calories) AS calories,
       protein_g, carbs_g, fat_g, serving_size,
       is_verified, source_post_url, price_php, price_source_url, source_images, provenance
"""

BRAND_ITEMS_SQL = text(_CATALOG_COLUMNS + """
FROM nutrition_catalog
WHERE brand_slug = :brand_slug AND is_active AND (is_verified OR CAST(:include_pending AS boolean))
ORDER BY calories ASC
LIMIT 250
"""
)

# Every restaurant's menu, filtered and ranked by the shared fragments. `match_count` is the
# number of matches before LIMIT. Search groups by brand; Chat ranks across restaurants.
CATALOG_SEARCH_SQL = text(
    "SELECT m.*, COUNT(*) OVER () AS match_count FROM (" + _CATALOG_COLUMNS
    + ", (" + _SORT_VALUE.format(n="") + ") AS sort_value FROM nutrition_catalog WHERE is_active AND "
    + _ITEM_FILTERS.format(n="") + """
) AS m
ORDER BY CASE WHEN CAST(:by_brand AS boolean) THEN lower(m.brand_name) END ASC,
         """ + _RANK_ORDER.format(v="m.sort_value") + """,
         lower(m.brand_name), lower(m.food_item), m.serving_size, m.item_id
LIMIT :limit
"""
)


def _source_evidence(provenance: dict | None) -> dict[str, Any]:
    metadata = provenance or {}
    bounds = metadata.get("calorie_bounds")
    return {"source_classification": metadata.get("source_classification", "unknown"),
            "calories_min": bounds[0] if bounds and bounds[0] != bounds[1] else None,
            "collected_at": metadata.get("collected_at"),
            "secondary_source_url": metadata.get("secondary_source_url"),
            "nutrition_source_url": metadata.get("nutrition_source_url")}


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
    pending_only: bool = False,
    descriptors: bool = True,
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
        "query_vec": to_pgvector_literal(list(query_vec)),
        "k": k,
        "verified": not pending_only,
        **item_filter_params(constraints, descriptors=descriptors),
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
            **_source_evidence(r["provenance"]),
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
    places: list[dict] | None = None, include_pending: bool = False,
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
                "include_pending": include_pending,
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
            has_catalog_data=bool(r["has_catalog_data"]),
            catalog_item_count=int(r["catalog_item_count"]),
            pending_item_count=int(r["pending_item_count"]),
            item_count=int(r["item_count"]),
            min_calories=r["min_calories"],
            max_protein_g=_f(r["max_protein_g"]),
        )
        for r in rows
    ]


async def brand_items(session: AsyncSession, brand_slug: str, *, include_pending: bool = False) -> list[CatalogItem]:
    rows = (await session.execute(BRAND_ITEMS_SQL, {"brand_slug": brand_slug, "include_pending": include_pending})).mappings().all()
    return _catalog_items(rows)


# Serving sizes and menu notes such as "With Rice and Drink" describe what an item includes, so
# search reads them. Clauses about what is left out ("no rice", "rice excluded") or about sourcing and review
# ("rice weight is not established by the source") are dropped first so they don't match.
NOTE_NOISE = (r"[^.;,]*\m(?:no|excluded|without|not included|establish\w*|review\w*|transcri\w*"
              r"|sources?|evidence|illustration|prices?|add-?ons?)\M[^.;,]*")


def effective_ranking(constraints: SearchConstraints) -> NutrientRanking | None:
    """The requested nutrient ranking; "heavy"/"light" wording ranks by calories."""
    if constraints.sort_by is not None:
        return NutrientRanking(field=constraints.sort_by, direction=constraints.sort_direction or "desc")
    order = search_order(constraints.semantic_query)
    if order == "semantic":
        return None
    return NutrientRanking(field="calories", direction="desc" if order == "calories_desc" else "asc")


def item_filter_params(constraints: SearchConstraints, *, descriptors: bool = True) -> dict[str, Any]:
    """Bound values for `_ITEM_FILTERS` and `_RANK_ORDER`, shared by every menu query."""
    patterns, match_any = catalog_patterns(constraints.semantic_query, constraints.target_brand,
                                           descriptors=descriptors)
    ranking = effective_ranking(constraints)
    snack = bool(re.search(r"\bsnacks?\b", constraints.semantic_query, re.I))
    return {
        "target_brand": constraints.target_brand,
        "max_cal": constraints.max_calories, "min_cal": constraints.min_calories,
        "min_protein": constraints.min_protein_g, "max_protein": constraints.max_protein_g,
        "min_carbs": constraints.min_carbs_g, "max_carbs": constraints.max_carbs_g,
        "min_fat": constraints.min_fat_g, "max_fat": constraints.max_fat_g,
        "max_price": constraints.max_price_php,
        "sort_by": ranking.field if ranking else None,
        "sort_dir": ranking.direction if ranking else None,
        "snack_window": bool(snack and ranking and ranking.field == "calories" and ranking.direction == "desc"),
        "patterns": patterns, "match_any": match_any, "note_noise": NOTE_NOISE,
        "exclude": catalog_exclusion(constraints.semantic_query),
    }


@dataclass(slots=True)
class CatalogPage:
    items: list[CatalogItem]
    total: int  # matches before the limit
    dropped: list[str] = field(default_factory=list)  # descriptor words left out because nothing had them


async def rank_catalog(
    session: AsyncSession, constraints: SearchConstraints, *, limit: int | None = None, by_brand: bool = False,
) -> CatalogPage:
    """Search every restaurant's menu: keywords, brand, nutrient limits and ranking.

    With a ranking, items missing that nutrient are excluded rather than treated as zero.
    """
    page = await _catalog_page(session, constraints, limit, by_brand, descriptors=True)
    dropped = descriptor_terms(constraints.semantic_query, constraints.target_brand)
    if page.total == 0 and dropped:
        # "grilled chicken": names rarely say "grilled", so fall back to the other words.
        page = await _catalog_page(session, constraints, limit, by_brand, descriptors=False)
        page.dropped = dropped if page.total else []
    return page


async def _catalog_page(session, constraints, limit, by_brand, *, descriptors: bool) -> CatalogPage:
    params = {**item_filter_params(constraints, descriptors=descriptors), "limit": limit, "by_brand": by_brand}
    rows = (await session.execute(CATALOG_SEARCH_SQL, params)).mappings().all()
    return CatalogPage(_catalog_items(rows), int(rows[0]["match_count"]) if rows else 0)


async def search_catalog(session: AsyncSession, constraints: SearchConstraints) -> list[CatalogItem]:
    """The Search tab: every match, grouped by restaurant."""
    return (await rank_catalog(session, constraints, by_brand=True)).items


def _catalog_items(rows) -> list[CatalogItem]:
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
            **_source_evidence(r["provenance"]),
        )
        for r in rows
    ]


def relaxation_ladder(c: SearchConstraints) -> list[tuple[str, SearchConstraints]]:
    """Explicit calorie, protein, price and distance limits remain hard constraints.

    The caller can submit a new request to broaden them; zero results never changes them.
    Retained as a compatibility hook for the inspector/response contract.
    """
    return []


def _trace(d: DiscoveryResult, discovery: Discovery) -> DiscoveryTrace:
    provider = d.provider or ("google" if isinstance(discovery, GooglePlacesDiscovery) else "osm")
    return DiscoveryTrace(**d.as_dict(), provider=provider)


def _takes_google_options(discovery: Discovery) -> bool:
    return isinstance(discovery, (GooglePlacesDiscovery, OsmFirstDiscovery))


class HybridRagService:
    def __init__(
        self,
        *,
        discovery: Discovery,
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
        filters: MealFilters | None = None,
        previous_message: str | None = None,
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
        # Only explicit follow-ups inherit context. A new dish starts a new search.
        previous_used_pin = False
        if previous_message:
            plain = unescape_for_parsing(sanitized.text)
            is_followup = bool(re.search(r"^(?:and\b|what about\b|how about\b|same\b|(?:(?:something|anything)\s+)?(?:lighter|heavier)\b)|\b(?:it|that)\b", plain.strip(), re.I))
            if is_followup and food_pattern(constraints.semantic_query, constraints.target_brand) is None:
                previous_plain = unescape_for_parsing(sanitize_user_text(previous_message).text)
                previous, _, _ = self.parser.regex.parse(previous_plain, self.brands.matcher,
                    default_radius_km or constraints.max_distance_km)
                previous_used_pin = mentions_location(previous_plain)
                semantic = previous.semantic_query
                if search_order(constraints.semantic_query) != "semantic":
                    semantic = re.sub(r"\b(?:heavy|heavier|hearty|filling|light|lighter|small|smaller|big|larger)\b", " ", semantic, flags=re.I)
                    semantic += " " + constraints.semantic_query
                inherited = ("target_brand", "max_calories", "min_calories", "max_price_php", "min_protein_g", "max_protein_g",
                             "min_carbs_g", "max_carbs_g", "min_fat_g", "max_fat_g")
                updates = {key:getattr(previous,key) for key in inherited if getattr(constraints,key) is None}
                if constraints.sort_by is None and previous.sort_by is not None:
                    updates.update(sort_by=previous.sort_by, sort_direction=previous.sort_direction)
                if not re.search(r"\b(?:km|kms|kilometers?|kilometres?|meters?|metres?|walking|walkable)\b", plain, re.I):
                    updates["max_distance_km"] = previous.max_distance_km
                constraints = constraints.model_copy(update={**updates,"semantic_query":semantic[:500]})
                parsed.warnings.append("Follow-up uses the previous meal request")
        if filters is not None:
            constraints = constraints.model_copy(update=filters.model_dump(exclude_none=True))
        validate_macro_physics(constraints)
        t = lap("parse", t0)

        def direct_reply(answer, items=None):
            timings["total"] = round((time.perf_counter() - t0) * 1000, 2)
            return ChatResponse(answer=answer, results=[], catalog_answers=items or [],
                relaxation=RelaxationInfo(final_constraints=constraints),
                inspector=InspectorTrace(request_id=request_id or str(uuid.uuid4()), sanitized_query=sanitized.text,
                    injection_flags=sanitized.flags, parser_used=parsed.parser_used, parser_warnings=parsed.warnings,
                    constraints=constraints, discovery=[], embedder=self.embedder.name, candidate_count=0,
                    catalog_candidate_count=len(items or []), retrieval_attempts=0,
                    generator_used="template", timings_ms=timings))

        greeting = help_answer(message)
        if greeting:
            return direct_reply(greeting)

        plain = unescape_for_parsing(sanitized.text)
        # Fiber, sugar, sodium... have no column: say so instead of ranking by something else.
        unstored = unstored_nutrients(plain)
        if unstored:
            return direct_reply(unstored_answer(unstored))

        ranking = effective_ranking(constraints)
        fact = question_field(message)
        if fact and constraints.sort_by is None:  # "most protein in KFC" ranks; "protein in a Zinger" looks up
            patterns, _ = catalog_patterns(constraints.semantic_query, constraints.target_brand)
            if not patterns and constraints.target_brand is None:
                return direct_reply("Which dish or restaurant do you mean? For example: ‘How many calories are in Conti’s Lemon Squares?’")
            items = (await rank_catalog(session, constraints, limit=5)).items
            return direct_reply(direct_answer(items, fact), items)

        # Chat searches every restaurant's menu, like Search. The pin is used only when asked
        # ("near me", "nearby", "within 2 km"), and even then only if some menu item matches.
        if not (mentions_location(plain) or previous_used_pin):
            return await self._catalog_reply(session, sanitized, parsed, constraints, ranking, plain,
                                             request_id, timings, t0)
        probe = await rank_catalog(session, constraints, limit=1)
        if probe.total == 0:
            return await self._catalog_reply(session, sanitized, parsed, constraints, ranking, plain,
                                             request_id, timings, t0)
        strict = not probe.dropped  # same descriptor fallback as the catalog-wide search

        # Stage 1: discovery (Overpass or cache).
        brand_filter = {"brand": constraints.target_brand} if _takes_google_options(self.discovery) else {}
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
                                places=discoveries[0].places, descriptors=strict)
        attempts = 1
        relaxation = RelaxationInfo()
        final = constraints

        catalog = RetrievalResult([], 0)
        if not result.items:
            catalog = await retrieve(session, lat=lat, lng=lng, constraints=constraints, query_vec=query_vec,
                                     places=discoveries[0].places, pending_only=True, descriptors=strict)

        # Keep exact pending matches before widening a search.
        if not result.items and not catalog.items:
            for note, relaxed in relaxation_ladder(constraints):
                if relaxed.max_distance_km > constraints.max_distance_km and len(discoveries) == 1:
                    discoveries.append(
                        await self.discovery.ensure_branches(session, lat, lng, relaxed.max_distance_km, **brand_filter)
                    )
                attempts += 1
                relaxation.steps.append(note)
                candidate = await retrieve(session, lat=lat, lng=lng, constraints=relaxed, query_vec=query_vec,
                                           places=discoveries[-1].places, descriptors=strict)
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
        if not result.items and not catalog.items and catalog_patterns(constraints.semantic_query, constraints.target_brand)[0]:
            answer = f"No menu items matching “{constraints.semantic_query}” meet your filters in this area. Try another food or browse all brands."
            if constraints.max_price_php is not None:
                answer += " Items without a checked price are excluded from budget searches."
        if catalog.items:
            answer = (
                f"{len(catalog.items)} menu {'item matches' if len(catalog.items) == 1 else 'items match'} your search (pending review). Expand a restaurant to view meals."
            )
        snack_reply = snack_answer(result.items or catalog.items, constraints.semantic_query, constraints.max_calories)
        if snack_reply:
            answer = snack_reply
        lap("generation", t)
        timings["total"] = round((time.perf_counter() - t0) * 1000, 2)

        inspector = InspectorTrace(
            request_id=request_id or str(uuid.uuid4()),
            sanitized_query=sanitized.text,
            injection_flags=sanitized.flags,
            parser_used=parsed.parser_used,
            parser_warnings=parsed.warnings,
            constraints=constraints,
            discovery=[_trace(d, self.discovery) for d in discoveries],
            embedder=self.embedder.name,
            candidate_count=result.candidate_count,
            catalog_candidate_count=catalog.candidate_count,
            retrieval_attempts=attempts,
            generator_used=generator_used,
            timings_ms=timings,
        )
        return ChatResponse(answer=answer, results=result.items, catalog_matches=catalog.items,
                            ranking=ranking, relaxation=relaxation, inspector=inspector)

    async def _catalog_reply(
        self, session: AsyncSession, sanitized, parsed, constraints: SearchConstraints,
        ranking: NutrientRanking | None, plain: str, request_id: str | None, timings: dict[str, float], t0: float,
    ) -> ChatResponse:
        """Answer from every restaurant's menu with the same retrieval as Search.

        Rankings use SQL ORDER BY on the stored nutrient, never embedding similarity, and leave
        out items that don't list that nutrient (counted and reported instead).
        """
        t = time.perf_counter()
        page = await rank_catalog(session, constraints, limit=RANKED_LIMIT if ranking else CHAT_MATCH_LIMIT,
                                  by_brand=ranking is None)
        missing = 0
        if ranking and ranking.field != "calories":  # calories are never missing
            # Same search without the ranking (so without its "must list this nutrient" filter).
            # Any calorie wording left in the query re-ranks by calories, which excludes nothing.
            unranked = constraints.model_copy(update={"sort_by": None, "sort_direction": None})
            missing = max((await rank_catalog(session, unranked, limit=1)).total - page.total, 0)
        brand_name = page.items[0].brand_name if page.items and constraints.target_brand else None
        if constraints.target_brand and brand_name is None:
            brand_name = (await session.execute(BRAND_NAME_SQL, {"slug": constraints.target_brand})).scalar()
        timings["retrieval"] = round((time.perf_counter() - t) * 1000, 2)

        terms = search_terms(constraints.semantic_query, constraints.target_brand, descriptors=not page.dropped)
        answer = menu_answer(page.items, page.total, constraints, ranking, missing_count=missing, brand_name=brand_name,
                             terms=terms, unknown_place=unknown_place(plain) if constraints.target_brand is None else None)
        snack = snack_answer(page.items, constraints.semantic_query, constraints.max_calories)
        if snack:
            answer = snack
        if page.dropped:
            words = " or ".join(f"“{word}”" for word in page.dropped)
            answer = f"No menu names mention {words}, so these results match your other words.\n{answer}"
        generator_used = "template"
        if ranking is None and page.items:
            # The model may only point at one retrieved row; the answer text stays database-built.
            pick = await self.generator.pick(sanitized.text, page.items[:SUGGESTION_CANDIDATES])
            if pick is not None:
                answer = f"Suggested match: {pick.food_item} — {pick.brand_name}.\n\n{answer}"
                generator_used = "llm"
        timings["total"] = round((time.perf_counter() - t0) * 1000, 2)
        return ChatResponse(
            answer=answer, results=[], menu_matches=page.items, menu_match_count=page.total, ranking=ranking,
            relaxation=RelaxationInfo(final_constraints=constraints),
            inspector=InspectorTrace(
                request_id=request_id or str(uuid.uuid4()), sanitized_query=sanitized.text,
                injection_flags=sanitized.flags, parser_used=parsed.parser_used, parser_warnings=parsed.warnings,
                constraints=constraints, discovery=[], embedder=self.embedder.name, candidate_count=0,
                catalog_candidate_count=page.total, retrieval_attempts=1, generator_used=generator_used,
                timings_ms=timings,
            ),
        )

    async def map_items(
        self, session: AsyncSession, *, lat: float, lng: float, radius_km: float, include_unmatched: bool, viewport=None,
        include_pending: bool = False, persist: bool = True,
    ) -> tuple[list[BranchPin], DiscoveryTrace]:
        options = {"spread_map": True, "viewport": viewport} if _takes_google_options(self.discovery) else {}
        if not persist and isinstance(self.discovery, (BranchDiscoveryService, OsmFirstDiscovery)):
            options["persist"] = False  # Google Places discovery never writes to the database
        discovery = await self.discovery.ensure_branches(session, lat, lng, radius_km, **options)
        if discovery.source == "live" and discovery.places is None:
            await self.brands.refresh(session)
        else:
            await self.brands.refresh_if_stale(session)
        pins = await nearby_branches(
            session, lat=lat, lng=lng, radius_km=radius_km, include_unmatched=include_unmatched,
            places=discovery.places, include_pending=include_pending,
        )
        return pins, _trace(discovery, self.discovery)
