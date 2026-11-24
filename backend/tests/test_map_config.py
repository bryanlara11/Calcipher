"""Provider selection keeps Google data off the no-key map and never exposes server keys."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import Settings
from app.main import build_rag_service, create_app, get_rag_service
from app.services.brand_matcher import BrandRegistry
from app.services.google_places import GooglePlacesDiscovery
from app.services.overpass_client import BranchDiscoveryService


@pytest.mark.parametrize("server_key,browser_key,provider", [
    (None, False, "osm"), (None, True, "osm"), ("synthetic-server-secret", False, "osm"),
    ("synthetic-server-secret", True, "google"),
])
async def test_config_requires_both_keys_and_never_exposes_secret(server_key, browser_key, provider):
    app = create_app(Settings(_env_file=None, google_places_api_key=server_key))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/v1/map-config", params={"browser_key_configured": str(browser_key).lower()})
    assert r.status_code == 200
    assert r.json()["provider"] == provider
    assert r.json()["google_places_configured"] == bool(server_key)
    assert "synthetic-server-secret" not in r.text


def test_provider_request_selects_matching_service():
    app = create_app(Settings(_env_file=None, google_places_api_key="synthetic-server-secret"))
    app.state.rag = SimpleNamespace(name="google")
    app.state.osm_rag = SimpleNamespace(name="osm")
    def request(provider):
        return Request({"type": "http", "app": app, "headers": [(b"x-calcipher-map-provider", provider.encode())]})
    assert get_rag_service(request("osm")).name == "osm"
    assert get_rag_service(request("google")).name == "google"
    with pytest.raises(HTTPException) as exc:
        get_rag_service(request("unknown"))
    assert exc.value.status_code == 400
    app.state.settings = Settings(_env_file=None, google_places_api_key=None)
    with pytest.raises(HTTPException) as exc:
        get_rag_service(request("google"))
    assert exc.value.status_code == 503


def test_factory_retains_google_and_configures_osm_cache():
    settings = Settings(_env_file=None, overpass_enabled=True)
    brands = BrandRegistry()
    assert isinstance(build_rag_service(settings, brands).discovery, GooglePlacesDiscovery)
    osm = build_rag_service(settings, brands, "osm").discovery
    assert isinstance(osm, BranchDiscoveryService)
    assert osm.enabled and osm.max_radius_m == settings.overpass_max_radius_m
    assert osm.failure_cooldown_s == settings.overpass_failure_cooldown_s
