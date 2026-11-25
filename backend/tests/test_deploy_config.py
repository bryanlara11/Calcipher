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
