import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CalorieMap from "./components/CalorieMap.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import RagInspector from "./components/RagInspector.jsx";
import SplashScreen from "./components/SplashScreen.jsx";
import { browserMapsKey, chat, mapConfig, mapItems } from "./api/client.js";
import { BrandLogo, CloseButton, Icon } from "./components/Brand.jsx";
import { useTheme } from "./components/ThemeControl.jsx";
import MealSearch from "./components/MealSearch.jsx";
import SettingsPage from "./components/SettingsPage.jsx";

const PAGES = { search: ["Search", "search"], nearby: ["Nearby", "pin"], chat: ["Chat", "chat"], settings: ["Settings", "sun"] };
function currentPage() { const page = window.location.hash.slice(2); return PAGES[page] ? page : "search"; }
const UP_MANILA = { lat: 14.5785, lng: 120.985 };
const EMPTY = [];
const formatRadius = meters => meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
const inPhilippines = ({ lat, lng }) => lat >= 4.5 && lat <= 21.5 && lng >= 116 && lng <= 127;

let nextId = 1;

export default function App() {
  const appearance = useTheme();
  const [page, setPage] = useState(currentPage);
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState(null);
  const [configRetry, setConfigRetry] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [mapView, setMapView] = useState("map");
  const [formValues, setFormValues] = useState({ food: "", calories: "", protein: "", budget: "" });
  const heading = useRef(null);
  useEffect(() => {
    const update = () => { setPage(currentPage()); };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    document.title = `${PAGES[page][0]} · CalCipher`;
    window.scrollTo(0, 0);
    heading.current?.focus({ preventScroll: true });
  }, [page]);
  useEffect(() => {
    const ctrl = new AbortController();
    mapConfig({ signal: ctrl.signal }).then(value => { setConfig(value); setConfigError(null); }).catch(error => {
      if (error.name !== "AbortError") { setConfig({ provider: "osm" }); setConfigError(error.message); }
    });
    return () => ctrl.abort();
  }, [configRetry]);
  function navigate(next) { window.location.hash = `/${next}`; }
  const provider = config?.provider === "google" && browserMapsKey ? "google" : "osm";
  const [location, setLocation] = useState(UP_MANILA);
  const [viewport, setViewport] = useState(null);
  const [searchViewport, setSearchViewport] = useState(null);
  const [retry, setRetry] = useState(0);
  const onViewport = useCallback(bounds => {
    setViewport(bounds);
    setSearchViewport(current => current ?? bounds);
  }, []);
  const [radiusMeters, setRadiusMeters] = useState(1000);
  const [previewMeters, setPreviewMeters] = useState(1000);
  const draggingRadius = useRef(false);
  const radiusKm = radiusMeters / 1000;
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [mapData, setMapData] = useState(null);
  const [mapBusy, setMapBusy] = useState(false);
  const [mapError, setMapError] = useState(null);
  const [mapErrorDismissed, setMapErrorDismissed] = useState(false);
  const [showInspector, setShowInspector] = useState(false);
  const [messages, setMessages] = useState([]);
  const [lastChat, setLastChat] = useState(null);
  const [busy, setBusy] = useState(false);
  const chatRequest = useRef(null);
  const cancelSearch = useCallback(() => {
    if (!chatRequest.current) return;
    chatRequest.current.abort();
    chatRequest.current = null;
    setBusy(false);
    setMessages(m => [...m, { id: nextId++, role: "error", title: "Search area changed", text: "Search again to use your new location or radius." }]);
  }, []);
  useEffect(() => () => chatRequest.current?.abort(), []);
  const [focus, setFocus] = useState(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [geoError, setGeoError] = useState(null);
  useEffect(() => {
    function escape(event) {
      if (event.key !== "Escape") return;
      if (showInspector) { setShowInspector(false); setInspectorOpen(false); }
      else setFiltersOpen(false);
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [showInspector]);
  const debounce = useRef();
  // While Google Places is cooling down (429), skip map requests entirely. Nothing refetches
  // automatically when it ends; the next pin move, radius change or Retry does.
  const [quotaUntil, setQuotaUntil] = useState(0);
  const quotaUntilRef = useRef(0);
  quotaUntilRef.current = quotaUntil;
  useEffect(() => {
    if (!quotaUntil) return;
    const t = setTimeout(() => setQuotaUntil(0), Math.max(0, quotaUntil - Date.now()));
    return () => clearTimeout(t);
  }, [quotaUntil]);
  const pins = useMemo(() => {
    const all = mapData?.branches ?? EMPTY;
    return verifiedOnly ? all.filter(pin => pin.has_calorie_data) : all;
  }, [mapData, verifiedOnly]);

  useEffect(() => {
    if (!searchViewport || page !== "nearby" || !config) return;
    const ctrl = new AbortController();
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      if (Date.now() < quotaUntilRef.current) return; // cooling down: don't even ask the backend
      setMapBusy(true);
      mapItems({ provider, ...location, radiusKm, viewport: searchViewport, includeUnmatched: true, signal: ctrl.signal })
        .then((d) => {
          if (ctrl.signal.aborted) return;
          setMapData(d);
          setMapError(null);
        })
        .catch((e) => {
          if (ctrl.signal.aborted || e.name === "AbortError") return;
          if (e.retryAfter && (e.code === "places_quota" || e.code === "places_unavailable" || e.status === 429)) {
            setQuotaUntil(Date.now() + e.retryAfter * 1000);
          }
          setMapError(e.message);
          setMapErrorDismissed(false); // a new failure shows again even if the last one was closed
        }).finally(() => { if (!ctrl.signal.aborted) setMapBusy(false); });
    }, 350);
    return () => {
      clearTimeout(debounce.current);
      ctrl.abort();
      setMapBusy(false);
    };
  }, [location, radiusKm, searchViewport, retry, page, config, provider]);

  const moveTo = useCallback((ll) => {
    const next = { lat: ll.lat, lng: ll.lng };
    if (!inPhilippines(next)) {
      setGeoError("CalCipher only covers the Philippines. Drop the pin inside the country.");
      return false;
    }
    setGeoError(null);
    cancelSearch();
    setLastChat(null);
    setMapData(null);
    setSearchViewport(viewport);
    setLocation(next);
    return true;
  }, [viewport, cancelSearch]);

  function applyRadius(meters) {
    if (meters === radiusMeters) return;
    cancelSearch();
    setLastChat(null);
    setMapData(null);
    setSearchViewport(viewport);
    setRadiusMeters(meters);
  }

  function finishRadiusDrag(event) {
    if (!draggingRadius.current) return;
    draggingRadius.current = false;
    applyRadius(event.currentTarget.valueAsNumber);
  }

  function useMyLocation() {
    if (!navigator.geolocation) {
      setGeoError("This browser can't share your location. Click the map to set it instead.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const ll = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        moveTo(ll);
        if (inPhilippines(ll)) setFocus({ ...ll, t: Date.now() });
      },
      () => setGeoError("Location permission was denied. Click the map to set your spot instead."),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  }

  async function send(text) {
    const ctrl = new AbortController();
    chatRequest.current = ctrl;
    setMessages((m) => [...m, { id: nextId++, role: "user", text }]);
    setBusy(true);
    try {
      const response = await chat({ provider, message: text, ...location, radiusKm, signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setLastChat(response);
      setMessages((m) => [...m, { id: nextId++, role: "assistant", response }]);
      if (response.results[0]) setFocus({ lat: response.results[0].latitude, lng: response.results[0].longitude, t: Date.now() });
    } catch (e) {
      if (ctrl.signal.aborted) return;
      const title =
        e.code === "macro_physics_violation" ? "That combination isn't possible"
        : e.code === "rate_limited" ? "Too many searches"
        : e.code === "network" ? "Backend unreachable"
        : "Search failed";
      setMessages((m) => [...m, { id: nextId++, role: "error", title, text: e.message }]);
    } finally {
      if (chatRequest.current === ctrl) { chatRequest.current = null; setBusy(false); }
    }
  }

  const alertText = geoError || (mapError && !mapErrorDismissed ? `Map data unavailable: ${mapError}` : null);
  function dismissAlert() {
    if (geoError) setGeoError(null);
    else setMapErrorDismissed(true);
  }

  function locate(item) {
    window.scrollTo(0, 0);
    setFocus({ lat: item.latitude, lng: item.longitude, t: Date.now() });
    navigate("nearby");
  }
  function searchVisibleArea() {
    if (viewport && viewport !== searchViewport) {
      const [south, north, west, east] = viewport.split(",").map(Number);
      if (!moveTo({ lat: (south + north) / 2, lng: (west + east) / 2 })) return;
    }
    setSearchViewport(viewport);
    setRetry(n => n + 1);
    setMapError(null);
  }
  const discoveryWarning = mapData?.discovery?.source === "disabled" ? "Restaurant discovery is switched off on the server."
    : mapData?.discovery?.error ? "Live restaurant discovery is unavailable. Showing any previously cached places. Try again later." : null;
  const nearbyStatus = mapBusy ? "Finding restaurants…" : mapError || discoveryWarning
    ? pins.length ? `${pins.length} saved restaurants · live search offline` : "Restaurant search couldn’t connect"
    : mapData ? `${pins.length} restaurant${pins.length === 1 ? "" : "s"} nearby` : "Choose an area to explore";
  const chatDiscoveryIssue = lastChat?.inspector?.discovery?.some(d => d.error || d.source === "disabled");
  return (
    <div className="page-shell">
      <SplashScreen />
      <a href="#page-content" className="skip-link" onClick={event => { event.preventDefault(); heading.current?.focus(); }}>Skip to content</a>
      <aside className="nav-rail">
        <a className="brand-lockup" href="#/search" aria-label="CalCipher home"><BrandLogo decorative /></a>
        <nav className="page-nav" aria-label="Main navigation">{Object.entries(PAGES).map(([id, [label, icon]]) =>
          <a key={id} href={`#/${id}`} aria-current={page === id ? "page" : undefined}><Icon name={icon} size={21} /><span>{label}</span></a>)}</nav>
      </aside>
      <div className="page-body">
        <main id="page-content" className={`page-content page-${page}`}>
          {page === "search" && <div className="search-layout">
            <section className="search-sheet">
              <h1 ref={heading} tabIndex={-1}>What sounds good?</h1>
              <p className="page-lead">Find something to eat nearby. Add nutrition limits or a budget if you have one.</p>
              <MealSearch savedValues={formValues} onValuesChange={setFormValues} busy={busy || !config} onSearch={query => { navigate("chat"); send(query); }} />
              <a className="search-location-link" href="#/nearby"><Icon name="pin" size={17} />Choose your search area<Icon name="arrow" size={17} /></a>
            </section>
            <aside className="search-side-note" aria-label="About meal data"><BrandLogo /><div className="note-rule" /><h2>About your<br />meal results</h2><p>Nutrition comes from the source catalog. A restaurant pin may have no nutrition data yet.</p><a href="#/nearby">Just browsing? Open the map <Icon name="arrow" size={17} /></a></aside>
            <div className="search-bottom-note"><Icon name="check" size={18} /><p>Missing nutrition and prices stay marked as unavailable.</p><a href="#/settings">About the data</a></div>
          </div>}
          {page === "nearby" && <section className="nearby-layout">
            <div className="page-title-row"><div><h1 ref={heading} tabIndex={-1}>Nearby restaurants</h1><p className="page-lead">Tap the map to move your pin, or use your location.</p></div><button className="button-secondary" type="button" aria-expanded={filtersOpen} aria-controls="area-controls" onClick={() => setFiltersOpen(v => !v)}><Icon name="filter" size={18} />Area & filters</button></div>
            {filtersOpen && <div className="area-controls" id="area-controls">
              <div className="radius-control"><div className="radius-heading"><label htmlFor="search-radius">Search radius</label><output htmlFor="search-radius">{formatRadius(previewMeters)}</output></div>
                <input id="search-radius" type="range" aria-label="Search radius" aria-valuetext={formatRadius(previewMeters)} min={100} max={5000} step={100} value={previewMeters}
                  onPointerDown={event => { draggingRadius.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
                  onChange={event => { const meters = event.currentTarget.valueAsNumber; setPreviewMeters(meters); if (!draggingRadius.current) applyRadius(meters); }}
                  onLostPointerCapture={finishRadiusDrag} onBlur={finishRadiusDrag} /><div className="range-limits"><span>100 m</span><span>5 km</span></div></div>
              <label className="nutrition-checkbox"><input type="checkbox" checked={verifiedOnly} onChange={event => setVerifiedOnly(event.target.checked)} />Only places with nutrition</label>
              <button type="button" onClick={useMyLocation} className="location-button"><Icon name="compass" size={17} />Use my location</button>
            </div>}
            <div className="nearby-toolbar"><p role="status">{nearbyStatus}</p><div className="nearby-actions">
              {(viewport !== searchViewport || mapError || discoveryWarning) && <button type="button" disabled={quotaUntil > 0 || mapBusy} className="text-button" onClick={searchVisibleArea}><Icon name="search" size={16} />{viewport !== searchViewport ? "Search this area" : "Retry search"}</button>}
              <div className="view-switch" role="group" aria-label="Nearby view">{["map", "list"].map(view => <button type="button" key={view} aria-pressed={mapView === view} onClick={() => setMapView(view)}>{view === "map" ? "Map" : "List"}</button>)}</div>
            </div></div>
            {alertText && <div role="alert" className="page-alert"><Icon name="info" size={18} /><p>{alertText}</p><CloseButton label="Dismiss message" onClick={dismissAlert} /></div>}
            <div className="nearby-map-shell" hidden={mapView !== "map"}><CalorieMap provider={provider} location={location} onMove={moveTo} onViewport={onViewport} radiusKm={previewMeters / 1000} pins={pins} results={lastChat?.results ?? EMPTY} focus={focus} theme={appearance.theme} />
              <div className="map-key"><span><i className="legend-dot nutrition-dot" />Nutrition</span><span><i className="legend-dot other-dot" />No nutrition yet</span><span><i className="legend-dot location-dot" />Your pin</span></div>
            </div>
            {mapView === "list" && <div className="nearby-list"><h2>Restaurants in this area</h2>{pins.length ? <ul>{pins.map(pin => <li key={pin.place_id ?? `${pin.osm_type}:${pin.osm_id}`}><button type="button" onClick={() => { locate(pin); setMapView("map"); }}><span><strong>{pin.poi_name}</strong><small>{pin.has_calorie_data ? `${pin.item_count} menu items with nutrition` : "Nutrition unavailable"}</small></span><span className="num">{pin.distance_km.toFixed(2)} km<Icon name="arrow" size={17} /></span></button></li>)}</ul> : <p>{mapBusy ? "Finding nearby places…" : mapError || discoveryWarning ? "Restaurant listings are temporarily unavailable." : "No restaurants found in this radius. Try a wider area."}</p>}{provider === "google" && <p>Restaurant locations from Google Maps.</p>}</div>}
            <p className="nearby-footnote">{provider === "osm" ? "OpenStreetMap locations · " : "Google Maps locations · "}Menus and source prices may vary by branch.</p>
          </section>}
          {page === "chat" && <section className="chat-page"><div className="page-title-row"><div><h1 ref={heading} tabIndex={-1}>Meal chat</h1></div><button className="text-button" type="button" onClick={() => { setShowInspector(true); setInspectorOpen(true); }}>Retrieval details</button></div><p className="chat-page-context">Searching within {formatRadius(radiusMeters)} of your pin. <a href="#/nearby">Change area</a></p><div className="chat-page-panel">{chatDiscoveryIssue && <p className="chat-connection-note" role="status"><Icon name="info" size={17} />Restaurant search couldn’t connect. <a href="#/settings">Connection details</a></p>}<ChatPanel messages={messages} onSend={send} busy={busy || !config} onLocate={locate} /></div></section>}
          {page === "settings" && <SettingsPage headingRef={heading} appearance={appearance} provider={provider} config={config} configError={configError} discoveryIssue={Boolean(mapData ? discoveryWarning || mapError : chatDiscoveryIssue || mapError)} onReconnect={() => setConfigRetry(n => n + 1)} browserKeyConfigured={Boolean(browserMapsKey)} />}

        </main>
      </div>
      {showInspector && <div className="inspector-layer"><RagInspector open={inspectorOpen} onToggle={() => setInspectorOpen(value => !value)} onClose={() => { setShowInspector(false); setInspectorOpen(false); }} chat={lastChat} map={mapData} /></div>}
    </div>
  );
}
