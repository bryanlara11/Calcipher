"""OSM-first discovery: Google answers only when Overpass fails and no saved OSM fetch covers the area."""
from app.services.discovery_fallback import OsmFirstDiscovery
from app.services.google_places import GooglePlacesDiscovery, PlacesUnavailable
from app.services.hybrid_rag import _trace
from app.services.overpass_client import DiscoveryResult


class FakeOsm:
    def __init__(self, result: DiscoveryResult, covered_by_old_fetch: bool = False):
        self.result, self.covered_by_old_fetch, self.coverage_checks = result, covered_by_old_fetch, []

    async def ensure_branches(self, session, lat, lng, radius_km, *, persist=True):
        self.persist = persist
        return self.result

    async def is_covered(self, session, lat, lng, radius_m, *, fresh_only=True):
        self.coverage_checks.append(fresh_only)
        return self.covered_by_old_fetch


class FakeGoogle:
    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, []

    async def ensure_branches(self, session, lat, lng, radius_km, **options):
        self.calls.append(options)
        if self.error:
            raise self.error
        return DiscoveryResult("live", False, round(radius_km * 1000), 20, 7, places=[{"brand_slug": "jollibee"}])


def failed_osm() -> DiscoveryResult:
    return DiscoveryResult("stale_cache_fallback", False, 2000, error="Overpass failed")


async def test_working_osm_never_calls_google():
    for source in ("cache", "live", "disabled"):
        google = FakeGoogle()
        result = await OsmFirstDiscovery(FakeOsm(DiscoveryResult(source, False, 2000)), google).ensure_branches(
            None, 14.58, 120.98, 2.0)
        assert result.source == source and result.provider is None
        assert google.calls == []


async def test_osm_outage_with_saved_coverage_keeps_the_fuller_osm_list():
    osm, google = FakeOsm(failed_osm(), covered_by_old_fetch=True), FakeGoogle()
    result = await OsmFirstDiscovery(osm, google).ensure_branches(None, 14.58, 120.98, 2.0)
    assert result.source == "stale_cache_fallback" and result.places is None
    assert osm.coverage_checks == [False]
    assert google.calls == []


async def test_osm_outage_without_coverage_answers_with_google_and_passes_options():
    google = FakeGoogle()
    result = await OsmFirstDiscovery(FakeOsm(failed_osm()), google).ensure_branches(
        None, 14.58, 120.98, 2.0, brand="jollibee", spread_map=True, viewport=[1, 2, 3, 4])
    assert result.provider == "google" and result.places and result.error is None
    assert google.calls == [{"brand": "jollibee", "spread_map": True, "viewport": [1, 2, 3, 4]}]


async def test_google_failure_returns_the_osm_outage_with_both_reasons():
    result = await OsmFirstDiscovery(FakeOsm(failed_osm()), FakeGoogle(PlacesUnavailable("quota reached"))).ensure_branches(
        None, 14.58, 120.98, 2.0)
    assert result.source == "stale_cache_fallback" and result.provider is None
    assert "Overpass failed" in result.error and "quota reached" in result.error


def test_trace_reports_the_provider_that_answered():
    wrapper = OsmFirstDiscovery(FakeOsm(failed_osm()), FakeGoogle())
    assert _trace(DiscoveryResult("cache", True, 2000), wrapper).provider == "osm"
    assert _trace(DiscoveryResult("live", False, 2000, provider="google"), wrapper).provider == "google"
    google = GooglePlacesDiscovery(None, None)
    assert _trace(DiscoveryResult("live", False, 2000), google).provider == "google"


async def test_device_location_lookup_passes_no_write_to_osm():
    osm = FakeOsm(DiscoveryResult("live", False, 2000))
    await OsmFirstDiscovery(osm, FakeGoogle()).ensure_branches(None, 14.58, 120.98, 2.0, persist=False)
    assert osm.persist is False
