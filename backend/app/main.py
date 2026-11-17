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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import PH_LAT_MAX, PH_LAT_MIN, PH_LNG_MAX, PH_LNG_MIN
from app.db.session import dispose_engine, get_engine, get_session, get_sessionmaker, init_db
from app.schemas import (
    MAX_DISTANCE_KM,
    BrandItemsResponse,
    ChatRequest,
    ChatResponse,
    MapItemsResponse,
)
from app.db.maintenance import count_stale_embeddings
from app.security import MacroPhysicsError, SlidingWindowRateLimiter
from app.services.brand_matcher import BrandRegistry, normalize_brand
from app.services.embedder import get_embedder
from app.services.generator import GroundedGenerator
from app.services.hybrid_rag import HybridRagService, brand_items
from app.services.llm import GeminiClient, LLMClient
from app.services.google_places import GooglePlacesDiscovery, PlacesQuotaLimited, PlacesUnavailable
from app.services.query_parser import QueryParser

logger = logging.getLogger("calcipher")

RATE_LIMITED_PREFIXES = ("/api/v1/chat", "/api/v1/map-items", "/api/v1/brands")


def build_rag_service(settings: Settings, brands: BrandRegistry) -> HybridRagService:
    llm: LLMClient | None = None
    if settings.gemini_api_key is not None:
        try:
            llm = GeminiClient(
                settings.gemini_api_key.get_secret_value(), settings.gemini_model, settings.llm_timeout_s
            )
        except Exception:  # missing package or bad key format: degrade to regex/template
            logger.exception("Gemini client unavailable; continuing without LLM")
    discovery = GooglePlacesDiscovery(settings.google_places_api_key, brands.refresh_if_stale)
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
    brands = BrandRegistry()
    async with get_sessionmaker()() as session:
        await brands.refresh(session)
        app.state.rag = build_rag_service(settings, brands)
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(title="CalCipher API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute, 60.0)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
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
        return _error(503, str(exc), "places_unavailable", request)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return _error(500, "Internal server error", "internal_error", request)

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

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, session: SessionDep, rag: RagDep) -> ChatResponse:
        return await rag.chat(
            session,
            message=body.message,
            lat=body.lat,
            lng=body.lng,
            default_radius_km=body.radius_km,
            request_id=request.state.request_id,
        )

    @app.get("/api/v1/map-items", response_model=MapItemsResponse)
    async def map_items(
        session: SessionDep,
        rag: RagDep,
        lat: Annotated[float, Query(ge=PH_LAT_MIN, le=PH_LAT_MAX)],
        lng: Annotated[float, Query(ge=PH_LNG_MIN, le=PH_LNG_MAX)],
        radius_km: Annotated[float, Query(gt=0, le=MAX_DISTANCE_KM)] = 2.0,
        include_unmatched: bool = True,
        viewport: Annotated[list[float] | None, Query(min_length=4, max_length=4)] = None,
    ) -> MapItemsResponse:
        if viewport is not None:
            south, north, west, east = viewport
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise HTTPException(422, "Invalid map viewport")
        pins, discovery = await rag.map_items(
            session, lat=lat, lng=lng, radius_km=radius_km, include_unmatched=include_unmatched, viewport=viewport,
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

    @app.get("/api/v1/brands/{brand_slug}/items", response_model=BrandItemsResponse)
    async def items_for_brand(
        session: SessionDep, brand_slug: Annotated[str, Path(min_length=1, max_length=255)]
    ) -> BrandItemsResponse:
        slug = normalize_brand(brand_slug)
        return BrandItemsResponse(brand_slug=slug, items=await brand_items(session, slug))

    return app


app = create_app()
