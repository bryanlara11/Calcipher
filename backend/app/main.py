"""FastAPI entrypoint: app factory, CORS, rate limiting, request IDs, error contract, routes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import PH_LAT_MAX, PH_LAT_MIN, PH_LNG_MAX, PH_LNG_MIN
from app.db.session import dispose_engine, get_engine, get_session, get_sessionmaker, init_db
from app.schemas import (
    DEFAULT_DISTANCE_KM,
    MAX_DISTANCE_KM,
    BrandItemsResponse,
    CatalogSearchRequest,
    CatalogSearchResponse,
    ChatRequest,
    ChatResponse,
    MapItemsRequest,
    MapItemsResponse,
    MealRatingRequest,
)
from app.db.maintenance import count_stale_embeddings
from app.security import MacroPhysicsError, SlidingWindowRateLimiter, validate_macro_physics
from app.services.brand_matcher import BrandRegistry, load_brand_matcher, normalize_brand
from app.services.embedder import get_embedder
from app.services.generator import GroundedGenerator
from app.services.hybrid_rag import HybridRagService, brand_items, search_catalog
from app.services.llm import GeminiClient, LLMClient
from app.services.discovery_fallback import OsmFirstDiscovery
from app.services.google_places import GooglePlacesDiscovery, PlacesQuotaLimited, PlacesUnavailable
from app.services.query_parser import QueryParser, RegexConstraintParser
from app.services.meal_ratings import attach_ratings, save_rating
from app.services.overpass_client import BranchDiscoveryService, OverpassClient

logger = logging.getLogger("calcipher")

RATE_LIMITED_PREFIXES = ("/api/v1/chat", "/api/v1/catalog-search", "/api/v1/map-items", "/api/v1/brands", "/api/v1/meal-ratings")


def build_rag_service(settings: Settings, brands: BrandRegistry, provider: str = "google") -> HybridRagService:
    location_brands = BrandRegistry(include_pending=True)
    llm: LLMClient | None = None
    if settings.gemini_api_key is not None:
        try:
            llm = GeminiClient(
                settings.gemini_api_key.get_secret_value(), settings.gemini_model, settings.llm_timeout_s
            )
        except Exception:  # missing package or bad key format: degrade to regex/template
            logger.exception("Gemini client unavailable; continuing without LLM")
    def google() -> GooglePlacesDiscovery:
        return GooglePlacesDiscovery(
            settings.google_places_api_key, location_brands.refresh_if_stale,
            cache_ttl_s=settings.places_cache_ttl_s, state_path=settings.places_state_path,
        )

    discovery: BranchDiscoveryService | GooglePlacesDiscovery | OsmFirstDiscovery
    if provider == "osm":
        discovery = BranchDiscoveryService(
            OverpassClient(settings.overpass_urls, user_agent=settings.overpass_user_agent,
                           timeout_s=settings.overpass_timeout_s),
            location_brands.refresh_if_stale, ttl_hours=settings.overpass_cache_ttl_hours,
            max_radius_m=settings.overpass_max_radius_m, enabled=settings.overpass_enabled,
            failure_cooldown_s=settings.overpass_failure_cooldown_s,
        )
        if settings.google_places_api_key and settings.google_places_mode == "fallback":
            discovery = OsmFirstDiscovery(discovery, google())
    else:
        discovery = google()
    return HybridRagService(
        discovery=discovery,
        parser=QueryParser(llm),
        embedder=get_embedder(),
        generator=GroundedGenerator(llm),
        brands=brands,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = get_engine()
    app.state.db_caps = await init_db(engine) if settings.auto_create_schema else {}
    brands = BrandRegistry(include_pending=True, include_archived=True)
    async with get_sessionmaker()() as session:
        await brands.refresh(session)
        from app.db.maintenance import rematch_branches
        await rematch_branches(session, await load_brand_matcher(session, include_pending=True))
        app.state.osm_rag = build_rag_service(settings, brands, "osm")
        google_primary = bool(settings.google_places_api_key) and settings.google_places_mode == "primary"
        app.state.rag = build_rag_service(settings, brands) if google_primary else app.state.osm_rag
        stale = await count_stale_embeddings(session, app.state.rag.embedder.name)
    if stale:
        logger.warning(
            "%d catalog row(s) were embedded by a different model than %s; semantic ranking is "
            "unreliable until you run `python -m app.ingest reembed`.",
            stale, app.state.rag.embedder.name,
        )
    logger.info(
        "CalCipher ready: %d brands, embedder=%s, llm=%s, google_places_configured=%s",
        len(brands.matcher), app.state.rag.embedder.name, settings.llm_enabled, bool(settings.google_places_api_key),
    )
    try:
        yield
    finally:
        await dispose_engine()


def get_rag_service(request: Request) -> HybridRagService:
    provider = request.headers.get("X-CalCipher-Map-Provider")
    if provider not in (None, "osm", "google"):
        raise HTTPException(status_code=400, detail="Unknown map provider")
    if provider == "google" and not request.app.state.settings.google_places_api_key:
        raise HTTPException(status_code=503, detail="Google Places is not configured")
    if provider == "osm":
        rag = getattr(request.app.state, "osm_rag", None)
        if rag is None:
            raise HTTPException(status_code=503, detail="OpenStreetMap discovery is starting up")
        return rag
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise HTTPException(status_code=503, detail="Service is starting up")
    return rag


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RagDep = Annotated[HybridRagService, Depends(get_rag_service)]


def _error(status: int, detail: str, code: str, request: Request, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "code": code, "request_id": getattr(request.state, "request_id", None)},
        headers=headers,
    )


class StripQueryFromAccessLog(logging.Filter):
    """Access logs keep method, path and status but drop query strings, which can carry coordinates."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, StripQueryFromAccessLog) for f in access_log.filters):
        access_log.addFilter(StripQueryFromAccessLog())

    app = FastAPI(title="CalCipher API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute, 60.0)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CalCipher-Map-Provider"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if incoming.isascii() and 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else str(uuid.uuid4())
        request.state.request_id = request_id
        if request.url.path.startswith(RATE_LIMITED_PREFIXES):
            client = request.client.host if request.client else "unknown"
            allowed, retry_after = request.app.state.rate_limiter.check(client)
            if not allowed:
                resp = _error(429, "Too many requests, slow down.", "rate_limited", request,
                              {"Retry-After": str(max(1, round(retry_after)))})
                resp.headers["X-Request-ID"] = request_id
                return resp
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(MacroPhysicsError)
    async def macro_physics_handler(request: Request, exc: MacroPhysicsError) -> JSONResponse:
        return _error(422, str(exc), exc.code, request)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _error(422, f"Invalid request: {loc} {first.get('msg', '')}".strip(), "validation_error", request)

    @app.exception_handler(PlacesUnavailable)
    async def places_unavailable(request: Request, exc: PlacesUnavailable) -> JSONResponse:
        if isinstance(exc, PlacesQuotaLimited):
            return _error(429, str(exc), "places_quota", request, {"Retry-After": str(exc.seconds)})
        headers = {"Retry-After": str(exc.seconds)} if exc.seconds else None
        return _error(503, str(exc), "places_unavailable", request, headers)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return _error(500, "Internal server error", "internal_error", request)

    @app.get("/api/v1/map-config")
    async def map_config(browser_key_configured: bool = False) -> dict[str, object]:
        # Only capability flags leave the server. Never return the server Places key.
        google_ready = (bool(settings.google_places_api_key) and browser_key_configured
                        and settings.google_places_mode == "primary")
        return {
            "provider": "google" if google_ready else "osm",
            "google_places_configured": bool(settings.google_places_api_key),
            "google_fallback": bool(settings.google_places_api_key) and settings.google_places_mode == "fallback",
            "osm_discovery_enabled": settings.overpass_enabled,
        }

    @app.get("/api/v1/health")
    async def health(request: Request, session: SessionDep) -> dict[str, object]:
        db_ok = True
        rag = getattr(request.app.state, "rag", None)
        stale: int | None = None
        try:
            await session.execute(text("SELECT 1"))
            if rag:
                stale = await count_stale_embeddings(session, rag.embedder.name)
        except Exception:
            db_ok = False
        return {
            "status": "ok" if db_ok and rag else "degraded",
            "database": db_ok,
            "pg_trgm": getattr(request.app.state, "db_caps", {}).get("pg_trgm", False),
            "embedder": rag.embedder.name if rag else None,
            "stale_embeddings": stale,
            "brands": len(rag.brands.matcher) if rag else 0,
            "llm_enabled": settings.llm_enabled,
            "google_places_configured": bool(settings.google_places_api_key),
        }

    @app.get("/api/v1/catalog-summary")
    async def catalog_summary(session: SessionDep) -> dict[str, int]:
        row = (await session.execute(text("""
            SELECT count(*) AS records, count(DISTINCT brand_slug) AS brands,
                   count(*) FILTER (WHERE is_verified) AS reviewed,
                   count(*) FILTER (WHERE NOT is_verified) AS pending
            FROM nutrition_catalog WHERE is_active
        """))).mappings().one()
        return dict(row)

    @app.get("/api/v1/catalog-brands")
    async def catalog_brands(session: SessionDep) -> dict:
        rows = (await session.execute(text("""
            SELECT brand_slug, min(brand_name) AS brand_name, count(*) AS records,
                   count(*) FILTER (WHERE is_verified) AS reviewed,
                   count(*) FILTER (WHERE NOT is_verified) AS pending
            FROM nutrition_catalog WHERE is_active GROUP BY brand_slug ORDER BY lower(min(brand_name))
        """))).mappings().all()
        return {"brands": [dict(row) for row in rows]}

    @app.post("/api/v1/catalog-search", response_model=CatalogSearchResponse)
    async def catalog_search(body: CatalogSearchRequest, session: SessionDep, rag: RagDep):
        constraints, _, _ = RegexConstraintParser().parse(body.message, rag.brands.matcher, DEFAULT_DISTANCE_KM)
        if body.filters:
            constraints = constraints.model_copy(update=body.filters.model_dump(exclude_none=True))
        validate_macro_physics(constraints)
        items = await search_catalog(session, constraints)
        return {"items": await attach_ratings(session, items), "filters": constraints.model_dump(include={
            "max_calories", "min_protein_g", "max_price_php"})}

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, session: SessionDep, rag: RagDep) -> ChatResponse:
        result = await rag.chat(
            session,
            message=body.message,
            lat=body.lat,
            lng=body.lng,
            default_radius_km=body.radius_km,
            filters=body.filters,
            request_id=request.state.request_id,
            previous_message=body.previous_message,
        )

        await attach_ratings(session, result.results + result.catalog_matches + result.catalog_answers)
        return result

    @app.post("/api/v1/meal-ratings/{item_id}")
    async def rate_meal(item_id: uuid.UUID, body: MealRatingRequest, session: SessionDep) -> dict:
        summary = await save_rating(session, item_id, body.voter_id, body.score)
        if summary is None:
            raise HTTPException(404, "Meal is not in the active catalog")
        return summary

    async def find_map_items(
        session: AsyncSession, rag: HybridRagService, *, lat: float, lng: float, radius_km: float,
        include_unmatched: bool, include_pending: bool, viewport: list[float] | None, persist: bool = True,
    ) -> MapItemsResponse:
        if viewport is not None:
            south, north, west, east = viewport
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise HTTPException(422, "Invalid map viewport")
        pins, discovery = await rag.map_items(
            session, lat=lat, lng=lng, radius_km=radius_km, include_unmatched=include_unmatched, viewport=viewport,
            include_pending=include_pending, persist=persist,
        )
        matched = sum(1 for p in pins if p.has_calorie_data)
        return MapItemsResponse(
            center={"lat": lat, "lng": lng},
            radius_km=radius_km,
            branches=pins,
            matched_count=matched,
            unmatched_count=len(pins) - matched,
            discovery=discovery,
        )

    @app.get("/api/v1/map-items", response_model=MapItemsResponse)
    async def map_items(
        session: SessionDep,
        rag: RagDep,
        lat: Annotated[float, Query(ge=PH_LAT_MIN, le=PH_LAT_MAX)],
        lng: Annotated[float, Query(ge=PH_LNG_MIN, le=PH_LNG_MAX)],
        radius_km: Annotated[float, Query(gt=0, le=MAX_DISTANCE_KM)] = 2.0,
        include_unmatched: bool = True,
        include_pending: bool = False,
        viewport: Annotated[list[float] | None, Query(min_length=4, max_length=4)] = None,
    ) -> MapItemsResponse:
        return await find_map_items(session, rag, lat=lat, lng=lng, radius_km=radius_km,
                                    include_unmatched=include_unmatched, include_pending=include_pending,
                                    viewport=viewport)

    # The same lookup with coordinates in the body, so they stay out of URLs and proxy/access logs.
    # device_location=true marks a pin taken from the browser's location; that lookup writes nothing.
    @app.post("/api/v1/map-items", response_model=MapItemsResponse)
    async def map_items_from_body(body: MapItemsRequest, session: SessionDep, rag: RagDep) -> MapItemsResponse:
        return await find_map_items(session, rag, lat=body.lat, lng=body.lng, radius_km=body.radius_km,
                                    include_unmatched=body.include_unmatched, include_pending=body.include_pending,
                                    viewport=body.viewport, persist=not body.device_location)

    @app.get("/api/v1/brands/{brand_slug}/items", response_model=BrandItemsResponse)
    async def items_for_brand(
        session: SessionDep, brand_slug: Annotated[str, Path(min_length=1, max_length=255)],
        include_pending: bool = False,
    ) -> BrandItemsResponse:
        slug = normalize_brand(brand_slug)
        return BrandItemsResponse(brand_slug=slug, items=await attach_ratings(session, await brand_items(session, slug, include_pending=include_pending)))

    # Mounted last so every /api route above wins. The frontend uses hash routing, so no SPA fallback is needed.
    if settings.static_dir is not None and (settings.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")

        # Phones must pick up a new release: revalidate the page every time, while the
        # content-hashed bundles under /assets/ can be cached for good.
        @app.middleware("http")
        async def frontend_cache_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            response = await call_next(request)
            path = request.url.path
            if not path.startswith("/api/"):
                immutable = path.startswith("/assets/") and response.status_code == 200
                response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache")
            return response

    return app


app = create_app()
