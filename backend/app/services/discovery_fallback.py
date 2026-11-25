"""OpenStreetMap-first branch discovery with Google Places as an outage fallback.

Overpass returns every mapped restaurant in an area, while Places Nearby Search returns at most 20,
so OSM stays the primary source. Google answers only when Overpass is failing AND no earlier OSM
fetch (even an expired one) covers the area. Restaurants rarely move, so an old OSM fetch is still
the more complete list.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.google_places import GooglePlacesDiscovery, PlacesUnavailable
from app.services.overpass_client import BranchDiscoveryService, DiscoveryResult

logger = logging.getLogger(__name__)


class OsmFirstDiscovery:
    def __init__(self, osm: BranchDiscoveryService, google: GooglePlacesDiscovery) -> None:
        self.osm = osm
        self.google = google

    async def ensure_branches(
        self, session: AsyncSession, lat: float, lng: float, radius_km: float,
        *, brand: str | None = None, spread_map: bool = False, viewport=None,
    ) -> DiscoveryResult:
        result = await self.osm.ensure_branches(session, lat, lng, radius_km)
        if result.source != "stale_cache_fallback":
            return result
        if await self.osm.is_covered(session, lat, lng, result.radius_m, fresh_only=False):
            return result
        try:
            fallback = await self.google.ensure_branches(
                session, lat, lng, radius_km, brand=brand, spread_map=spread_map, viewport=viewport,
            )
        except PlacesUnavailable as exc:
            result.error = f"{result.error}; Google fallback unavailable: {exc}"[:300]
            return result
        logger.info("Overpass unavailable and no saved OSM coverage; answered with Google Places")
        fallback.provider = "google"
        return fallback
