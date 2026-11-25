"""Stage 1 — nearby restaurant discovery via the free OpenStreetMap Overpass API.

`OverpassClient` performs the HTTP call (descriptive User-Agent, Semaphore(1), mirror fallback)
and parses the JSON. `BranchDiscoveryService` wraps it with a 24-hour spatial grid cache in
PostgreSQL (`overpass_fetch_log`) and upserts matched POIs into `osm_branch_cache`. When Overpass
is unreachable, requests fall back to whatever branches are already cached.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    PH_LAT_MAX,
    PH_LAT_MIN,
    PH_LNG_MAX,
    PH_LNG_MIN,
    OsmBranchCache,
    OverpassFetchLog,
)
from app.services.brand_matcher import BrandMatcher

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
GRID_CELL_DEG = 0.01  # ~1.1 km cells
# Half the diagonal of a 0.01° cell at the equator (~786 m), rounded up. Fetching
# `requested + GRID_PAD_M` around the snapped center covers every point inside the cell.
GRID_PAD_M = 800
_UPSERT_CHUNK = 500
QUERY_CACHE_PREFIX = "food-v2:"

# A single Overpass request in flight per process (OSM fair-use policy).
OVERPASS_SEMAPHORE = asyncio.Semaphore(1)

_QUERY_TEMPLATE = (
    '[out:json][timeout:{server_timeout_s}];('
    'nwr["amenity"~"restaurant|fast_food|cafe"](around:{radius_m},{lat:.6f},{lng:.6f});'
    'nwr["shop"="convenience"]["brand"~"7[ -]?eleven",i](around:{radius_m},{lat:.6f},{lng:.6f});'
    'nwr["shop"="convenience"]["name"~"7[ -]?eleven",i](around:{radius_m},{lat:.6f},{lng:.6f});'
    ");out center;"
)


class OverpassUnavailableError(RuntimeError):
    """All Overpass endpoints failed (timeout, 429, 5xx, malformed payload)."""


@dataclass(frozen=True, slots=True)
class OsmPoi:
    osm_type: Literal["node", "way", "relation"]
    osm_id: int
    poi_name: str
    brand_tag: str | None
    amenity_type: str | None
    latitude: float
    longitude: float
    address_hint: str | None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) enclosing the circle; slightly generous."""
    dlat = math.degrees(radius_km / EARTH_RADIUS_KM) * 1.01
    dlng = dlat / max(math.cos(math.radians(lat)), 1e-6)
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def snap_to_grid(lat: float, lng: float, cell_deg: float = GRID_CELL_DEG) -> tuple[float, float, str]:
    i, j = math.floor(lat / cell_deg), math.floor(lng / cell_deg)
    return round((i + 0.5) * cell_deg, 6), round((j + 0.5) * cell_deg, 6), f"{i}:{j}"


def circle_contains(
    outer_lat: float, outer_lng: float, outer_radius_m: float,
    lat: float, lng: float, radius_m: float,
) -> bool:
    return haversine_km(outer_lat, outer_lng, lat, lng) * 1000 + radius_m <= outer_radius_m


def in_philippines(lat: float, lng: float) -> bool:
    return PH_LAT_MIN <= lat <= PH_LAT_MAX and PH_LNG_MIN <= lng <= PH_LNG_MAX


# ---------------------------------------------------------------------------
# HTTP client + parsing
# ---------------------------------------------------------------------------


def build_query(lat: float, lng: float, radius_m: int, server_timeout_s: int = 15) -> str:
    lat, lng, radius_m = float(lat), float(lng), int(radius_m)
    if not (math.isfinite(lat) and math.isfinite(lng)) or radius_m <= 0:
        raise ValueError("invalid Overpass query parameters")
    return _QUERY_TEMPLATE.format(radius_m=radius_m, lat=lat, lng=lng, server_timeout_s=int(server_timeout_s))


def server_timeout_for(client_timeout_s: float) -> int:
    """Overpass-side query limit: at least 15 s, and 5 s under the client wait so its answer still arrives."""
    return max(15, int(client_timeout_s) - 5)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def parse_overpass_elements(payload: dict[str, Any]) -> list[OsmPoi]:
    """Parse `out center` JSON: nodes carry lat/lon, ways/relations carry `center`."""
    pois: dict[tuple[str, int], OsmPoi] = {}
    for el in payload.get("elements") or []:
        if not isinstance(el, dict):
            continue
        osm_type, osm_id = el.get("type"), el.get("id")
        if osm_type not in ("node", "way", "relation") or not isinstance(osm_id, int):
            continue
        if (osm_type, osm_id) in pois:  # keep the first (complete) copy of a repeated element
            continue
        if osm_type == "node":
            lat, lng = _as_float(el.get("lat")), _as_float(el.get("lon"))
        else:
            center = el.get("center") if isinstance(el.get("center"), dict) else {}
            lat, lng = _as_float(center.get("lat")), _as_float(center.get("lon"))
        if lat is None or lng is None or not in_philippines(lat, lng):
            continue
        tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
        brand = (tags.get("brand") or "").strip() or None
        name = (tags.get("name") or tags.get("name:en") or brand or "").strip()
        if not name:
            continue  # unnamed POIs are useless for brand resolution and for the map
        address = ", ".join(
            v.strip() for v in (tags.get("addr:street"), tags.get("addr:city")) if isinstance(v, str) and v.strip()
        )
        pois[(osm_type, osm_id)] = OsmPoi(
            osm_type=osm_type,
            osm_id=osm_id,
            poi_name=name[:255],
            brand_tag=brand[:255] if brand else None,
            amenity_type=(tags.get("amenity") or None),
            latitude=lat,
            longitude=lng,
            address_hint=address or None,
        )
    return list(pois.values())


class OverpassClient:
    def __init__(
        self,
        urls: list[str],
        *,
        user_agent: str,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not urls:
            raise ValueError("at least one Overpass URL is required")
        self.urls = list(urls)
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self._transport = transport

    async def fetch_pois(self, lat: float, lng: float, radius_m: int) -> list[OsmPoi]:
        query = build_query(lat, lng, radius_m, server_timeout_for(self.timeout_s))
        errors: list[str] = []
        async with OVERPASS_SEMAPHORE:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                transport=self._transport,
            ) as client:
                for url in self.urls:
                    try:
                        resp = await client.post(url, data={"data": query})
                    except httpx.HTTPError as exc:
                        errors.append(f"{url}: {exc.__class__.__name__}")
                        continue
                    if resp.status_code == 200:
                        try:
                            payload = resp.json()
                        except ValueError:
                            errors.append(f"{url}: invalid JSON")
                            continue
                        if not isinstance(payload, dict) or "elements" not in payload:
                            errors.append(f"{url}: unexpected payload")
                            continue
                        remark = str(payload.get("remark") or "")
                        if "error" in remark.lower():
                            # Overpass reports query timeouts as 200 + remark; results are partial.
                            errors.append(f"{url}: {remark[:120]}")
                            continue
                        return parse_overpass_elements(payload)
                    errors.append(f"{url}: HTTP {resp.status_code}")
                    if resp.status_code == 429 or resp.status_code >= 500:
                        continue  # overloaded/rate limited -> next mirror
                    break  # other 4xx means our query is wrong; mirrors would reject it too
        raise OverpassUnavailableError("; ".join(errors) or "no Overpass endpoint attempted")


# ---------------------------------------------------------------------------
# Cache-aware discovery service
# ---------------------------------------------------------------------------

DiscoverySource = Literal["cache", "live", "stale_cache_fallback", "disabled"]


@dataclass(slots=True)
class DiscoveryResult:
    source: DiscoverySource
    cache_hit: bool
    radius_m: int
    fetched_count: int = 0
    matched_count: int = 0
    purged_seed_rows: int = 0
    error: str | None = None
    elapsed_ms: float = 0.0
    # None selects the legacy database source. An empty live result stays empty.
    places: list[dict[str, Any]] | None = None
    # Set when a wrapper answered with a provider other than the configured one (Google fallback).
    provider: Literal["google", "osm"] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "cache_hit": self.cache_hit,
            "fetched_count": self.fetched_count,
            "matched_count": self.matched_count,
            "purged_seed_rows": self.purged_seed_rows,
            "radius_m": self.radius_m,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


MatcherProvider = Callable[[AsyncSession], Awaitable[BrandMatcher]]


class BranchDiscoveryService:
    def __init__(
        self,
        client: OverpassClient | None,
        matcher_provider: MatcherProvider,
        *,
        ttl_hours: int = 24,
        max_radius_m: int = 5000,
        enabled: bool = True,
        failure_cooldown_s: float = 300.0,
    ) -> None:
        self.client = client
        self.matcher_provider = matcher_provider
        self.ttl = timedelta(hours=ttl_hours)
        self.max_radius_m = max_radius_m
        self.enabled = enabled and client is not None
        self._single_flight = asyncio.Lock()
        # Circuit breaker: after a failed fetch, serve the cache immediately instead of making
        # every request wait out the mirror timeouts again.
        self.failure_cooldown_s = failure_cooldown_s
        self._cooldown_until = 0.0

    def _cooling_down(self) -> bool:
        return time.monotonic() < self._cooldown_until

    async def is_covered(
        self, session: AsyncSession, lat: float, lng: float, radius_m: int, *, fresh_only: bool = True
    ) -> bool:
        """True when a past Overpass fetch covers the circle; with fresh_only=False, expired fetches count too."""
        cutoff = datetime.now(timezone.utc) - self.ttl if fresh_only else datetime.min.replace(tzinfo=timezone.utc)
        pad_km = (radius_m + self.max_radius_m + GRID_PAD_M) / 1000
        min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, pad_km)
        rows = await session.execute(
            select(OverpassFetchLog.center_lat, OverpassFetchLog.center_lng, OverpassFetchLog.radius_m).where(
                OverpassFetchLog.fetched_at >= cutoff,
                OverpassFetchLog.grid_key.startswith(QUERY_CACHE_PREFIX),
                OverpassFetchLog.center_lat.between(min_lat, max_lat),
                OverpassFetchLog.center_lng.between(min_lng, max_lng),
            )
        )
        return any(circle_contains(c_lat, c_lng, r, lat, lng, radius_m) for c_lat, c_lng, r in rows)

    async def ensure_branches(
        self, session: AsyncSession, lat: float, lng: float, radius_km: float
    ) -> DiscoveryResult:
        started = time.perf_counter()
        radius_m = max(1, min(round(radius_km * 1000), self.max_radius_m))
        result = await self._ensure(session, lat, lng, radius_m)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

    async def _ensure(self, session: AsyncSession, lat: float, lng: float, radius_m: int) -> DiscoveryResult:
        if await self.is_covered(session, lat, lng, radius_m):
            return DiscoveryResult("cache", True, radius_m)
        if not self.enabled:
            return DiscoveryResult("disabled", False, radius_m)
        cooldown = DiscoveryResult(
            "stale_cache_fallback", False, radius_m,
            error="Overpass failed recently; using cached branches until the cooldown ends",
        )
        if self._cooling_down():
            return cooldown

        async with self._single_flight:
            # Another request may have fetched this area (or hit a failure) while we waited.
            if await self.is_covered(session, lat, lng, radius_m):
                return DiscoveryResult("cache", True, radius_m)
            if self._cooling_down():
                return cooldown
            c_lat, c_lng, grid_key = snap_to_grid(lat, lng)
            grid_key = QUERY_CACHE_PREFIX + grid_key
            fetch_radius = radius_m + GRID_PAD_M
            assert self.client is not None
            try:
                pois = await self.client.fetch_pois(c_lat, c_lng, fetch_radius)
            except OverpassUnavailableError as exc:
                self._cooldown_until = time.monotonic() + self.failure_cooldown_s
                logger.warning("Overpass unavailable, serving cached branches: %s", exc)
                return DiscoveryResult("stale_cache_fallback", False, radius_m, error=str(exc)[:300])

            matcher = await self.matcher_provider(session)
            now = datetime.now(timezone.utc)
            rows = []
            for poi in pois:
                match = matcher.match(poi.poi_name, poi.brand_tag)
                rows.append(
                    {
                        "osm_type": poi.osm_type,
                        "osm_id": poi.osm_id,
                        "poi_name": poi.poi_name,
                        "matched_brand_slug": match.slug if match else None,
                        "amenity_type": poi.amenity_type,
                        "latitude": poi.latitude,
                        "longitude": poi.longitude,
                        "address_hint": poi.address_hint,
                        "last_fetched_at": now,
                    }
                )
            await upsert_branches(session, rows)
            purged = await purge_seed_rows(session, c_lat, c_lng, fetch_radius) if rows else 0
            session.add(
                OverpassFetchLog(
                    grid_key=grid_key,
                    center_lat=c_lat,
                    center_lng=c_lng,
                    radius_m=fetch_radius,
                    element_count=len(rows),
                    fetched_at=now,
                )
            )
            await session.commit()
            matched = sum(1 for r in rows if r["matched_brand_slug"])
            logger.info("Overpass fetched %d POIs (%d matched)", len(rows), matched)
            return DiscoveryResult("live", False, radius_m, len(rows), matched, purged)


async def upsert_branches(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), _UPSERT_CHUNK):
        chunk = rows[start : start + _UPSERT_CHUNK]
        stmt = insert(OsmBranchCache).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="pk_osm_branch_cache",
            set_={
                c: stmt.excluded[c]
                for c in ("poi_name", "matched_brand_slug", "amenity_type", "latitude",
                          "longitude", "address_hint", "last_fetched_at")
            },
        )
        await session.execute(stmt)


async def purge_seed_rows(session: AsyncSession, lat: float, lng: float, radius_m: int) -> int:
    """Remove approximate seed branches inside an area that now has real OSM data."""
    min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius_m / 1000)
    candidates = await session.execute(
        select(OsmBranchCache.osm_id, OsmBranchCache.latitude, OsmBranchCache.longitude).where(
            OsmBranchCache.osm_type == "seed",
            OsmBranchCache.latitude.between(min_lat, max_lat),
            OsmBranchCache.longitude.between(min_lng, max_lng),
        )
    )
    ids = [oid for oid, b_lat, b_lng in candidates if haversine_km(lat, lng, b_lat, b_lng) * 1000 <= radius_m]
    if not ids:
        return 0
    await session.execute(
        delete(OsmBranchCache).where(and_(OsmBranchCache.osm_type == "seed", OsmBranchCache.osm_id.in_(ids)))
    )
    return len(ids)
