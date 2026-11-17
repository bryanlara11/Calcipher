"""Request-scoped Google Places discovery. Listings are joined to the local catalog in SQL.

Quota protection (every Places call costs quota):
- identical searches within `cache_ttl_s` reuse the previous response (pin rounded to ~11 m);
- concurrent identical searches share one in-flight request;
- after a 429 every caller fails fast until the cooldown ends. Without a Retry-After header the
  cooldown doubles on consecutive 429s (1, 2, 4 ... 30 min) and resets after a success. Other
  Google errors pause calls for 30 s. The cooldown is saved to `state_path`, so restarting the
  API does not immediately send another request while Google is still refusing them.
"""
import asyncio
import json
import logging
from collections import OrderedDict
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import time

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.overpass_client import DiscoveryResult, MatcherProvider, bounding_box, haversine_km, in_philippines


logger = logging.getLogger(__name__)

MIN_REVIEW_COUNT = 5
BASE_BACKOFF_S = 60
MAX_BACKOFF_S = 30 * 60
ERROR_PAUSE_S = 30
CACHE_MAX_ENTRIES = 256


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
    def __init__(self, message: str, seconds: int | None = None):
        super().__init__(message)
        self.seconds = seconds  # sent as Retry-After when set


class PlacesQuotaLimited(PlacesUnavailable):
    def __init__(self, seconds):
        super().__init__(f"Google Places reached a usage limit. Try again in {seconds} seconds. "
                         "If this persists, check the project’s Places API quota in Google Cloud.", seconds)


def retry_delay(header) -> int | None:
    """Seconds from a Retry-After header, or None if absent/unparseable."""
    if header:
        try:
            return max(1, int(header))
        except ValueError:
            try:
                return max(1, math.ceil(parsedate_to_datetime(header).timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return None


def _cache_key(method: str, body: dict) -> str:
    """Round the circle center to 4 decimals (~11 m) so tiny pin nudges reuse the response.
    Results are still distance-filtered against the caller's exact pin."""
    normalized = json.loads(json.dumps(body))
    for field in ("locationRestriction", "locationBias"):
        center = normalized.get(field, {}).get("circle", {}).get("center")
        if center:
            center["latitude"] = round(center["latitude"], 4)
            center["longitude"] = round(center["longitude"], 4)
    return method + json.dumps(normalized, sort_keys=True)


class GooglePlacesDiscovery:
    def __init__(self, key: SecretStr | None, matcher_provider: MatcherProvider, *, transport=None,
                 cache_ttl_s: float = 300, state_path: Path | None = None, clock=time.time):
        self.key = key
        self.matcher_provider = matcher_provider
        self.transport = transport
        self.cache_ttl_s = cache_ttl_s
        self.state_path = state_path
        self.clock = clock  # wall clock, so a persisted cooldown means the same thing after restart
        self.retry_at = 0.0
        self.retry_reason = "quota"
        self.strikes = 0  # consecutive 429s, drives the back-off
        self.http_calls = 0
        self._cache: OrderedDict[str, tuple[float, list]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._load_state()

    # ----- cooldown state -------------------------------------------------------------

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.is_file():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.retry_at = float(state.get("retry_at", 0))
            self.retry_reason = str(state.get("reason", "quota"))
            self.strikes = int(state.get("strikes", 0))
        except (OSError, ValueError, TypeError):
            logger.warning("Ignoring unreadable Places cooldown file %s", self.state_path)

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"retry_at": self.retry_at, "reason": self.retry_reason,
                                       "strikes": self.strikes}), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError:
            logger.warning("Could not save Places cooldown to %s", self.state_path)

    def _pause(self, seconds: int, reason: str) -> None:
        self.retry_at = max(self.retry_at, self.clock() + seconds)
        self.retry_reason = reason
        self._save_state()

    def _check_cooldown(self) -> None:
        remaining = math.ceil(self.retry_at - self.clock())
        if remaining <= 0:
            return
        if self.retry_reason == "quota":
            raise PlacesQuotaLimited(remaining)
        raise PlacesUnavailable(f"Google Places returned an error; searches resume in {remaining} seconds.",
                                remaining)

    # ----- fetching -------------------------------------------------------------------

    async def _fetch(self, method: str, body: dict) -> list:
        self.http_calls += 1
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
                    header = retry_delay(response.headers.get("Retry-After"))
                    seconds = header or min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2 ** self.strikes)
                    self.strikes += 1
                    self._pause(seconds, "quota")
                    logger.warning("Google Places 429; pausing Places calls for %s s", seconds)
                    raise PlacesQuotaLimited(seconds)
                response.raise_for_status()
                places = response.json().get("places", [])
        except httpx.HTTPStatusError as exc:
            self._pause(ERROR_PAUSE_S, "error")
            raise PlacesUnavailable(f"Google Places returned HTTP {exc.response.status_code}. "
                                    "Check the Places API (New) key, billing and quota.", ERROR_PAUSE_S) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise PlacesUnavailable("Google Places could not be reached or returned an invalid response.") from exc
        if self.strikes or self.retry_at:
            self.strikes, self.retry_at = 0, 0.0
            self._save_state()
        return places

    async def _places(self, method: str, body: dict) -> tuple[list, bool]:
        """Return (raw places, from_cache). Cache first, then join an in-flight call, then fetch."""
        key = _cache_key(method, body)
        hit = self._cache.get(key)
        if hit and hit[0] > self.clock():
            self._cache.move_to_end(key)
            return hit[1], True
        self._check_cooldown()
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch(method, body))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        places = await asyncio.shield(task)
        if self.cache_ttl_s > 0:
            self._cache[key] = (self.clock() + self.cache_ttl_s, places)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
        return places, False

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
        places, from_cache = await self._places(method, body)

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
        return DiscoveryResult("cache" if from_cache else "live", from_cache, radius, len(rows),
                               sum(bool(r["matched_brand_slug"]) for r in rows.values()),
                               elapsed_ms=(time.perf_counter() - started) * 1000, places=list(rows.values()))
