"""Request-scoped Google Places discovery. Listings are joined to the local catalog in SQL."""
from email.utils import parsedate_to_datetime
import math
import time

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.overpass_client import DiscoveryResult, MatcherProvider, bounding_box, haversine_km, in_philippines


MIN_REVIEW_COUNT = 5


def viewport_circle(lat, lng, radius_km, viewport):
    south, north, west, east = bounding_box(lat, lng, radius_km)
    vs, vn, vw, ve = viewport
    south, north = max(south, vs), min(north, vn)
    west, east = max(west, vw), min(east, ve)
    if south >= north or west >= east:
        return None
    y, x = (south + north) / 2, (west + east) / 2
    meters = math.ceil(haversine_km(y, x, south, west) * 1000)
    if meters >= radius_km * 1000:
        return {"center": {"latitude": lat, "longitude": lng}, "radius": round(radius_km * 1000)}
    return {"center": {"latitude": y, "longitude": x}, "radius": max(1, meters)}


class PlacesUnavailable(RuntimeError):
    pass


class PlacesQuotaLimited(PlacesUnavailable):
    def __init__(self, seconds):
        self.seconds = seconds
        super().__init__(f"Google Places reached a usage limit. Try again in {seconds} seconds. "
                         "If this persists, check the project’s Places API quota in Google Cloud.")


def retry_delay(header):
    if header:
        try:
            return max(1, int(header))
        except ValueError:
            try:
                return max(1, math.ceil(parsedate_to_datetime(header).timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return 60


class GooglePlacesDiscovery:
    def __init__(self, key: SecretStr | None, matcher_provider: MatcherProvider, *, transport=None):
        self.key = key
        self.matcher_provider = matcher_provider
        self.transport = transport
        self.retry_at = 0.0

    async def ensure_branches(self, session: AsyncSession, lat: float, lng: float, radius_km: float,
                              *, brand: str | None = None,
                              spread_map: bool = False, viewport=None) -> DiscoveryResult:
        if not self.key:
            raise PlacesUnavailable("Set GOOGLE_PLACES_API_KEY in backend/.env and restart the API.")
        started = time.perf_counter()
        radius = round(radius_km * 1000)
        circle = {"center": {"latitude": lat, "longitude": lng}, "radius": radius}
        if spread_map and viewport and not brand:
            circle = viewport_circle(lat, lng, radius_km, viewport)
            if circle is None:
                return DiscoveryResult("live", False, radius, places=[])
        remaining = math.ceil(self.retry_at - time.monotonic())
        if remaining > 0:
            raise PlacesQuotaLimited(remaining)
        if brand:
            method = "searchText"
            body = {"textQuery": brand, "locationBias": {"circle": circle}, "pageSize": 20,
                    "includedType": "restaurant", "strictTypeFiltering": True,
                    "regionCode": "PH", "languageCode": "en"}
        else:
            method = "searchNearby"
            body = {"includedPrimaryTypes": ["restaurant"],
                    "rankPreference": "POPULARITY" if spread_map else "DISTANCE", "maxResultCount": 20,
                    "locationRestriction": {"circle": circle}, "languageCode": "en"}
        try:
            async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
                response = await client.post(
                    f"https://places.googleapis.com/v1/places:{method}", json=body,
                    headers={"X-Goog-Api-Key": self.key.get_secret_value(),
                             "X-Goog-FieldMask": "places.id,places.displayName,places.location,"
                                                 "places.primaryType,places.formattedAddress,places.attributions,"
                                                 "places.userRatingCount"},
                )
                if response.status_code == 429:
                    seconds = retry_delay(response.headers.get("Retry-After"))
                    self.retry_at = max(self.retry_at, time.monotonic() + seconds)
                    raise PlacesQuotaLimited(seconds)
                response.raise_for_status()
                places = response.json().get("places", [])
        except httpx.HTTPStatusError as exc:
            raise PlacesUnavailable(f"Google Places returned HTTP {exc.response.status_code}. "
                                    "Check the Places API (New) key, billing and quota.") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise PlacesUnavailable("Google Places could not be reached or returned an invalid response.") from exc

        matcher = await self.matcher_provider(session)
        rows = {}
        for place in places:
            reviews = place.get("userRatingCount")
            if not isinstance(reviews, int) or reviews < MIN_REVIEW_COUNT:
                continue
            primary = place.get("primaryType") or ""
            if not (primary.endswith("_restaurant") or primary in {"restaurant", "steak_house", "diner", "food_court", "bar_and_grill", "buffet_restaurant"}):
                continue
            point = place.get("location", {})
            latitude, longitude = point.get("latitude"), point.get("longitude")
            name = place.get("displayName", {}).get("text")
            if not (place.get("id") and name and latitude is not None and longitude is not None
                    and in_philippines(latitude, longitude)):
                continue
            if haversine_km(lat, lng, latitude, longitude) > radius_km:
                continue
            match = matcher.match(name)
            rows[place["id"]] = {
                "place_id": place["id"], "poi_name": name, "matched_brand_slug": match.slug if match else None,
                "amenity_type": place.get("primaryType"), "address_hint": place.get("formattedAddress"),
                "latitude": latitude, "longitude": longitude, "attributions": place.get("attributions", []),
            }
        return DiscoveryResult("live", False, radius, len(rows), sum(bool(r["matched_brand_slug"]) for r in rows.values()),
                               elapsed_ms=(time.perf_counter() - started) * 1000, places=list(rows.values()))
