"""Single-service hosting: host-provided database URLs and the same-origin frontend."""
import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("given", [
    "postgres://u:p@db.internal:5432/calcipher",
    "postgresql://u:p@db.internal:5432/calcipher",
    "postgresql+psycopg://u:p@db.internal:5432/calcipher",
])
def test_host_database_urls_use_psycopg_driver(given):
    settings = Settings(_env_file=None, database_url=given)
    assert settings.database_url == "postgresql+psycopg://u:p@db.internal:5432/calcipher"


async def test_static_frontend_is_served_without_shadowing_api(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>CalCipher</title>", encoding="utf-8")
    app = create_app(Settings(_env_file=None, static_dir=tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/")
        config = await client.get("/api/v1/map-config")
    assert page.status_code == 200 and "CalCipher" in page.text
    assert config.status_code == 200 and config.json()["provider"] == "osm"


async def test_missing_static_dir_leaves_api_only(tmp_path):
    app = create_app(Settings(_env_file=None, static_dir=tmp_path / "absent"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).status_code == 404


def test_access_log_drops_query_strings_with_coordinates():
    import logging
    from app.main import StripQueryFromAccessLog
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("10.0.0.1:1", "GET", "/api/v1/map-items?lat=14.601&lng=120.988", "1.1", 200), None)
    StripQueryFromAccessLog().filter(record)
    assert "14.601" not in record.getMessage() and "/api/v1/map-items" in record.getMessage()


def test_map_items_body_validates_coverage_radius_and_extra_fields():
    import pydantic
    from app.schemas import MapItemsRequest
    ok = MapItemsRequest(lat=14.601, lng=120.988, radius_km=1, device_location=True)
    assert ok.device_location and ok.include_pending is False
    for bad in ({"lat": 35.68, "lng": 139.77}, {"lat": 14.6, "lng": 120.98, "radius_km": 0},
                {"lat": 14.6, "lng": 120.98, "radius_km": 50}, {"lat": 14.6, "lng": 120.98, "accuracy": 5},
                {"lat": 14.6, "lng": 120.98, "viewport": [1, 2, 3]}):
        with pytest.raises(pydantic.ValidationError):
            MapItemsRequest(**bad)


async def test_page_revalidates_while_hashed_assets_cache(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>CalCipher</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    app = create_app(Settings(_env_file=None, static_dir=tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/")
        bundle = await client.get("/assets/index-abc123.js")
        api = await client.get("/api/v1/map-config")
    assert page.headers["cache-control"] == "no-cache"
    assert "immutable" in bundle.headers["cache-control"]
    assert "cache-control" not in api.headers
