import { RatingsContext, useMealRatings } from "./ratings.js";
import CatalogBrands, { CatalogSearchResults } from "./components/CatalogBrands.jsx";
import { usePreferences } from "./preferences.js";
import { restaurantCategory, RESTAURANT_CATEGORIES } from "./restaurantCategories.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CalorieMap from "./components/CalorieMap.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import RagInspector from "./components/RagInspector.jsx";
import { browserMapsKey, chat, mapConfig, mapItems, searchCatalog } from "./api/client.js";
import { BrandLogo, CloseButton, Icon } from "./components/Brand.jsx";
import { useTheme } from "./components/ThemeControl.jsx";
import MealSearch from "./components/MealSearch.jsx";
import SettingsPage from "./components/SettingsPage.jsx";
import HomePage from "./components/HomePage.jsx";
import FavoritesPage from "./components/FavoritesPage.jsx";
import BrandDirectory from "./components/BrandDirectory.jsx";
import { useFavorites } from "./favorites.js";
import MenuDataPage from "./components/MenuDataPage.jsx";
import MealPlanPage from "./components/MealPlanPage.jsx";
import { MealPlanContext, useMealPlan } from "./mealPlan.js";
import { coarseLocation, locationErrorMessage, locationNote, locationPermission, radiusViewport, readDeviceLocation } from "./deviceLocation.js";

const PAGES = { home: ["Home", "home"], search: ["Search", "search"], nearby: ["Nearby", "pin"], chat: ["Chat", "chat"], plan: ["Meal plan", "calendar"], favorites: ["Favorites", "heart"], settings: ["Settings", "sun"], data: ["About menu data", "info"] };
function currentPage() { const page = window.location.hash.slice(2); return PAGES[page] ? page : "home"; }
const UP_MANILA = { lat: 14.5785, lng: 120.985 };
const EMPTY = [];
const formatRadius = meters => meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
const inPhilippines = ({ lat, lng }) => lat >= 4.5 && lat <= 21.5 && lng >= 116 && lng <= 127;

const messageId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

export default function App() {
  const appearance = useTheme();
  const [preferences, setPreference] = usePreferences();
  const [notice, setNotice] = useState(null);
  const notify = useCallback(text => setNotice({ text }), []);
  useEffect(() => {
    if (!notice) return;
    const timeout = setTimeout(() => setNotice(null), 3500);
    return () => clearTimeout(timeout);
  }, [notice]);
  const mealPlan = useMealPlan(notify);
  const mealRatings = useMealRatings();
  const { favorites, isFavorite, toggleFavorite: changeFavorite, storageUnavailable } = useFavorites();
  function toggleFavorite(pin) {
    if (changeFavorite(pin)) {
      notify(isFavorite(pin) ? `Removed ${pin.poi_name} from favorites.` : `Added ${pin.poi_name} to favorites.`);
    }
  }
  const [page, setPage] = useState(currentPage);
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState(null);
  const [configRetry, setConfigRetry] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [mapView, setMapView] = useState("map");
  const effectiveMapView = preferences.mapsEnabled ? mapView : "list";
  const [formValues, setFormValues] = useState({ food: "", calories: "", protein: "", budget: "" });
  const [searchResponse, setSearchResponse] = useState(null);
  const [searchError, setSearchError] = useState(null);
  const [searchStarted, setSearchStarted] = useState(false);
  const [searchBusy, setSearchBusy] = useState(false);
  const catalogRequest = useRef(null);
  useEffect(() => () => catalogRequest.current?.abort(), []);
  const [activeGoals, setActiveGoals] = useState({});
  const searchResultsRef = useRef(null);
  useEffect(() => {
    if (searchResponse || searchError) searchResultsRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [searchResponse, searchError]);
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
  const [pinFromDevice, setPinFromDevice] = useState(false); // pin came from the device's location
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
    setMessages(m => [...m, { id: messageId(), role: "error", title: "Search area changed", text: "Search again to use your new location or radius." }]);
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
  // Which restaurant menus are open, by stable branch ID. Memory only.
  const [openMenus, setOpenMenus] = useState(() => new Set());
  const toggleMenu = useCallback(key => setOpenMenus(current => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  }), []);
  const [restaurantQuery, setRestaurantQuery] = useState("");
  const [categorySelection, setCategory] = useState("all");
  const category = ["Fast food", "Pizza"].includes(categorySelection) ? "Fast food & pizza" : categorySelection;
  const [sortOrder, setSortOrder] = useState("az");
  const catalogPins = useMemo(() => (mapData?.branches ?? EMPTY).filter(pin => pin.has_catalog_data || pin.has_calorie_data), [mapData]);
  const pins = useMemo(() => catalogPins.filter(pin =>
    (category === "all" || restaurantCategory(pin) === category)
    && `${pin.brand_name} ${pin.poi_name}`.toLowerCase().includes(restaurantQuery.trim().toLowerCase())
  ), [catalogPins, category, restaurantQuery]);

  useEffect(() => {
    // A visible map reports the area it shows; wait for that. In List view a hidden map can't
    // report anything, so search the pin's radius instead of waiting forever.
    const waitForMap = !searchViewport && preferences.mapsEnabled && effectiveMapView === "map";
    if (waitForMap || page !== "nearby" || !config) return;
    const searchBox = searchViewport ?? radiusViewport(location, radiusKm * 1000);
    const ctrl = new AbortController();
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      if (Date.now() < quotaUntilRef.current) return; // cooling down: don't even ask the backend
      setMapBusy(true);
      mapItems({ provider, ...location, radiusKm, viewport: searchBox, includeUnmatched: false, includePending: true, deviceLocation: pinFromDevice, signal: ctrl.signal })
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
  }, [location, radiusKm, searchViewport, retry, page, config, provider, preferences.mapsEnabled, pinFromDevice, effectiveMapView]);

  const placePin = useCallback((next, nextSearchViewport) => {
    setGeoError(null);
    cancelSearch();
    setLastChat(null);
    setMapData(null);
    setSearchViewport(nextSearchViewport);
    setLocation(next);
  }, [cancelSearch]);

  // Device location lives only in memory. A reading is used once to place the pin; the pin it
  // replaced is kept so leaving Nearby can put it back. `locateToken` discards a late reading after
  // a manual pin move or leaving the page.
  const [locating, setLocating] = useState(false);
  const locateToken = useRef(0);
  const locatingRef = useRef(false);
  const pinBeforeDevice = useRef(null);
  const manualPin = useRef(false);
  const locationRef = useRef(location);
  locationRef.current = location;
  const stopLocating = useCallback(() => {
    locateToken.current += 1;
    locatingRef.current = false;
    setLocating(false);
  }, []);

  const moveTo = useCallback((ll) => {
    const next = { lat: ll.lat, lng: ll.lng };
    if (!inPhilippines(next)) {
      setGeoError("CalCipher only covers the Philippines. Drop the pin inside the country.");
      return false;
    }
    stopLocating();
    pinBeforeDevice.current = null;
    manualPin.current = true; // a spot the user chose: don't replace it with an automatic reading
    setPinFromDevice(false);
    placePin(next, viewport);
    return true;
  }, [viewport, placePin, stopLocating]);

  // Leaving Nearby forgets any device-derived pin, results and pending reading.
  useEffect(() => {
    if (page === "nearby") return;
    stopLocating();
    if (!pinBeforeDevice.current) return;
    const previous = pinBeforeDevice.current;
    pinBeforeDevice.current = null;
    setPinFromDevice(false);
    setFocus(null);
    // Give the restored pin its own search box: a hidden map (List view) can't report one.
    placePin(previous, radiusViewport(previous, radiusMeters));
  }, [page, placePin, stopLocating, radiusMeters]);

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

  function locateDevice() {
    if (!preferences.locationEnabled || locatingRef.current || page !== "nearby") return;
    const token = locateToken.current + 1;
    locateToken.current = token;
    locatingRef.current = true;
    setLocating(true);
    setGeoError(null);
    const finish = () => { locatingRef.current = false; setLocating(false); };
    readDeviceLocation(coords => {
      if (token !== locateToken.current) return; // pin moved or page left meanwhile
      finish();
      const next = coarseLocation(coords, radiusMeters);
      if (!Number.isFinite(next.lat) || !Number.isFinite(next.lng) || !inPhilippines(next)) {
        setGeoError(locationErrorMessage("outside", preferences.mapsEnabled));
        return;
      }
      if (!pinBeforeDevice.current) pinBeforeDevice.current = locationRef.current;
      manualPin.current = false;
      setPinFromDevice(true);
      placePin(next, radiusViewport(next, radiusMeters));
      setFocus({ ...next, t: Date.now() });
    }, code => {
      if (token !== locateToken.current) return;
      finish();
      setGeoError(locationErrorMessage(code, preferences.mapsEnabled));
    });
  }
  const locateDeviceRef = useRef(locateDevice);
  locateDeviceRef.current = locateDevice;

  // Once the browser already allows location for this site (granted after a tap), opening
  // Nearby uses it automatically. This never shows a prompt: with "prompt", "denied" or no
  // Permissions API it waits for a tap. A pin the user placed by hand this visit is kept.
  const autoLocateTried = useRef(false);
  useEffect(() => {
    if (page !== "nearby") { autoLocateTried.current = false; return; }
    if (autoLocateTried.current || !config || !preferences.locationEnabled || manualPin.current) return;
    autoLocateTried.current = true;
    locationPermission().then(state => { if (state === "granted") locateDeviceRef.current(); });
  }, [page, config, preferences.locationEnabled]);

  async function searchMenus(message, filters) {
    catalogRequest.current?.abort();
    const ctrl = new AbortController();
    catalogRequest.current = ctrl;
    setSearchStarted(true);
    setSearchBusy(true);
    setSearchResponse(null);
    setSearchError(null);
    try {
      const response = await searchCatalog({ message, filters, signal: ctrl.signal });
      if (!ctrl.signal.aborted) setSearchResponse(response);
    } catch (error) {
      if (!ctrl.signal.aborted) setSearchError(error.code === "network" ? `Backend unreachable: ${error.message}` : error.message);
    } finally {
      if (!ctrl.signal.aborted) setSearchBusy(false);
    }
  }

  async function send(text) {
    if (chatRequest.current) return;
    const ctrl = new AbortController();
    const searchContext = { ...location, radiusKm, provider };
    chatRequest.current = ctrl;
    setMessages((m) => [...m, { id: messageId(), role: "user", text }]);
    setBusy(true);
    try {
      const previous = [...messages].reverse().find(m => m.role === "assistant" && m.requestText && m.searchContext
        && ["lat", "lng", "radiusKm", "provider"].every(key => m.searchContext[key] === searchContext[key]));
      const response = await chat({ provider, message: text, ...location, radiusKm, signal: ctrl.signal,
        previousMessage: previous?.requestText });
      if (ctrl.signal.aborted) return;
      setLastChat(response);
      setActiveGoals(response.inspector?.constraints ?? {});
      const context = response.inspector?.constraints;
      const requestText = context ? [context.semantic_query, context.target_brand,
        context.max_calories != null && `under ${context.max_calories} kcal`,
        context.min_protein_g != null && `at least ${context.min_protein_g} g protein`,
        context.max_price_php != null && `under PHP${context.max_price_php}`,
        `within ${context.max_distance_km} km`].filter(Boolean).join(" ").slice(0,500) : text;
      setMessages((m) => [...m, { id: messageId(), role: "assistant", response, searchContext, requestText }]);
      if (response.results[0]) setFocus({ lat: response.results[0].latitude, lng: response.results[0].longitude, t: Date.now() });
    } catch (e) {
      if (ctrl.signal.aborted) return;
      const title =
        e.code === "macro_physics_violation" ? "That combination isn't possible"
        : e.code === "rate_limited" ? "Too many searches"
        : e.code === "network" ? "Backend unreachable"
        : "Search failed";
      setMessages((m) => [...m, { id: messageId(), role: "error", title, text: e.message }]);
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
  function exploreFavorite(pin) {
    if (moveTo({ lat: pin.latitude, lng: pin.longitude })) {
      setFocus({ lat: pin.latitude, lng: pin.longitude, t: Date.now() });
      navigate("nearby");
    }
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
    : mapData ? pins.length ? `${pins.length} catalog location${pins.length === 1 ? "" : "s"} nearby` : "No catalog locations found in this area" : "Choose an area to explore";
  const chatDiscoveryIssue = lastChat?.inspector?.discovery?.some(d => d.error || d.source === "disabled");
  // Google answers only as an OpenStreetMap outage fallback. Its places must be shown on a Google map
  // (or as a list) with Google attribution, never on the OpenStreetMap map.
  const googleAnswered = mapData?.discovery?.provider === "google";
  const mapProvider = googleAnswered ? "google" : provider;
  const mapAllowed = preferences.mapsEnabled && !(googleAnswered && !browserMapsKey);
  const nearbyView = mapAllowed ? effectiveMapView : "list";
  const chatUsedGoogle = lastChat?.inspector?.discovery?.some(d => d.provider === "google");
  return (
    <RatingsContext.Provider value={mealRatings}><MealPlanContext.Provider value={mealPlan}><div className="page-shell">
      <a href="#page-content" className="skip-link" onClick={event => { event.preventDefault(); heading.current?.focus(); }}>Skip to content</a>
      <header className="app-header"><div className="app-header-inner">
        <a className="app-brand" href="#/home" aria-label="CalCipher home"><BrandLogo decorative /></a>
        <nav className="app-navigation" aria-label="Main navigation">{Object.entries(PAGES).filter(([id]) => !["home", "settings", "data"].includes(id)).map(([id, [label, icon]]) =>
          <a key={id} href={`#/${id}`} aria-current={page === id ? "page" : undefined}><Icon name={icon} size={21} /><span>{label}</span></a>)}</nav>
        <a className="settings-shortcut" href="#/settings" aria-label="Settings" title="Settings" aria-current={page === "settings" ? "page" : undefined}><Icon name="sun" size={21} /><span>Settings</span></a>
      </div></header>
      <div className="page-body">
        <main id="page-content" className={`page-content page-${page}`}>
          {page === "home" && <HomePage headingRef={heading} />}
          {page === "data" && <MenuDataPage headingRef={heading} />}
          {page === "plan" && <MealPlanPage headingRef={heading} />}
          {page === "search" && <div className="search-layout">
            <section className="search-sheet">
              <h1 ref={heading} tabIndex={-1}>Search menus</h1>
              <p className="page-lead">Look up a dish or browse by restaurant.</p>
              <MealSearch savedValues={formValues} onValuesChange={setFormValues} busy={searchBusy} onSearch={searchMenus} />
            </section>
            {searchStarted && <section ref={searchResultsRef} className="search-results" aria-label="Meal search results"><h2>Meal results</h2>
              {searchBusy && <p role="status">Searching menus…</p>}
              {searchError && <p role="alert" className="page-alert">{searchError}</p>}
              {searchResponse && <CatalogSearchResults items={searchResponse.items} filters={searchResponse.filters} />}
            </section>}
            <CatalogBrands goals={searchResponse?.filters} />
          </div>}
          {page === "nearby" && <section className="nearby-layout">
            <div className="page-title-row"><div><h1 ref={heading} tabIndex={-1}>Nearby restaurants</h1><p className="page-lead">Find a branch, look through its menu or save it for later.</p></div><button className="button-secondary" type="button" aria-expanded={filtersOpen} aria-controls="area-controls" onClick={() => setFiltersOpen(v => !v)}><Icon name="filter" size={18} />Area & filters</button></div>
            {filtersOpen && <div className="area-controls" id="area-controls">
              <div className="radius-control"><div className="radius-heading"><label htmlFor="search-radius">Search radius</label><output htmlFor="search-radius">{formatRadius(previewMeters)}</output></div>
                <input id="search-radius" type="range" aria-label="Search radius" aria-valuetext={formatRadius(previewMeters)} min={100} max={5000} step={100} value={previewMeters}
                  onPointerDown={event => { draggingRadius.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
                  onChange={event => { const meters = event.currentTarget.valueAsNumber; setPreviewMeters(meters); if (!draggingRadius.current) applyRadius(meters); }}
                  onLostPointerCapture={finishRadiusDrag} onBlur={finishRadiusDrag} /><div className="range-limits"><span>100 m</span><span>5 km</span></div></div>
              <div className="nearby-category-controls">
                <label className="field">Category<select value={category} onChange={event => setCategory(event.target.value)}><option value="all">All categories</option>{RESTAURANT_CATEGORIES.map(value => <option key={value}>{value}</option>)}</select></label>
                <label className="field">Sort<select value={sortOrder} onChange={event => setSortOrder(event.target.value)}><option value="az">A–Z</option><option value="za">Z–A</option><option value="nearest">Nearest first</option></select></label>
              </div>
            </div>}
            <div className="nearby-search-tools">
              <label className="field">Search restaurants<input type="search" placeholder="Restaurant or brand name" value={restaurantQuery} onChange={event => setRestaurantQuery(event.target.value)} /></label>
            </div>
            {category !== "all" && <button className="active-category text-button" type="button" onClick={() => setCategory("all")} aria-label={`Clear ${category} category`}>{category}<Icon name="close" size={14} /></button>}
            <div className="nearby-locate">
              <button type="button" className="location-button" onClick={locateDevice} disabled={!preferences.locationEnabled || locating} aria-busy={locating} aria-describedby="locate-note"><Icon name="compass" size={17} />{locating ? "Finding your location…" : "Use my location"}</button>
              <p id="locate-note">{preferences.locationEnabled ? locationNote(mapProvider, Boolean(config?.google_fallback)) : <>Location requests are off. <a href="#/settings">Change in Settings</a></>}{pinFromDevice && " Your pin is at your device’s approximate location until you leave Nearby."}</p>
            </div>
            <div className="nearby-toolbar"><p role="status">{locating ? "Finding your location…" : nearbyStatus}</p><div className="nearby-actions">
              {(viewport !== searchViewport || mapError || discoveryWarning) && <button type="button" disabled={quotaUntil > 0 || mapBusy} className="text-button" onClick={searchVisibleArea}><Icon name="search" size={16} />{viewport !== searchViewport ? "Search this area" : "Retry search"}</button>}
              <div className="view-switch" role="group" aria-label="Nearby view">{["map", "list"].map(view => <button type="button" key={view} disabled={view === "map" && !mapAllowed} aria-pressed={nearbyView === view} onClick={() => setMapView(view)}>{view === "map" ? "Map" : "List"}</button>)}</div>
            </div></div>
            {alertText && <div role="alert" className="page-alert"><Icon name="info" size={18} /><p>{alertText}</p><CloseButton label="Dismiss message" onClick={dismissAlert} /></div>}
            {mapAllowed && <div className="nearby-map-shell" hidden={nearbyView !== "map"}><CalorieMap provider={mapProvider} location={location} onMove={moveTo} onViewport={onViewport} radiusKm={previewMeters / 1000} pins={pins} results={lastChat?.results ?? EMPTY} focus={focus} theme={appearance.theme} goals={activeGoals} isFavorite={isFavorite} onToggleFavorite={toggleFavorite} />
              <div className="map-key"><span><i className="legend-dot nutrition-dot" />Reviewed</span><span><i className="legend-dot other-dot" />Pending review</span><span><i className="legend-dot location-dot" />Your pin</span></div>
            </div>}
            {nearbyView === "list" && <div className="nearby-list"><h2>Restaurants by brand</h2>{pins.length ? <BrandDirectory key={`${restaurantQuery}-${category}-${sortOrder}`} sortOrder={sortOrder} pins={pins} openMenus={openMenus} onToggleMenu={toggleMenu} goals={activeGoals} isFavorite={isFavorite} onToggleFavorite={toggleFavorite} onLocate={pin => { locate(pin); if (preferences.mapsEnabled) setMapView("map"); }} /> : <p>{mapBusy ? "Finding nearby places…" : mapError || discoveryWarning ? "Restaurant listings are temporarily unavailable." : !mapData ? "Finding nearby places…" : "No catalog locations in this area. Try a wider radius."}</p>}{mapProvider === "google" && <p>Restaurant locations from Google Maps.</p>}</div>}
            <p className="nearby-footnote">{mapProvider === "osm" ? "OpenStreetMap locations · " : "Google Maps locations · "}Menus and source prices may vary by branch.</p>
          </section>}
          {page === "chat" && <section className="chat-page"><div className="page-title-row"><div><h1 ref={heading} tabIndex={-1}>Meal chat</h1></div>{preferences.developerTools && <button className="text-button" type="button" onClick={() => { setShowInspector(true); setInspectorOpen(true); }}>Retrieval details</button>}</div><p className="chat-page-context">Searching within {formatRadius(radiusMeters)} of your pin. <a href="#/nearby">Change area</a></p>{preferences.developerTools && showInspector && <div className="inspector-layer"><RagInspector open={inspectorOpen} onToggle={() => setInspectorOpen(value => !value)} onClose={() => { setShowInspector(false); setInspectorOpen(false); }} chat={lastChat} map={mapData} /></div>}<div className="chat-page-panel">{chatDiscoveryIssue && <p className="chat-connection-note" role="status"><Icon name="info" size={17} />Restaurant search couldn’t connect. <a href="#/settings">Connection details</a></p>}{chatUsedGoogle && <p className="chat-connection-note" role="status">Restaurant locations from Google Maps.</p>}<ChatPanel messages={messages} onSend={send} busy={busy || !config} onLocate={locate} searchContext={{ ...location, radiusKm, provider }} isFavorite={isFavorite} onToggleFavorite={toggleFavorite} /></div></section>}
          {page === "favorites" && <FavoritesPage headingRef={heading} favorites={favorites} provider={provider} onExplore={exploreFavorite} onToggle={toggleFavorite} storageUnavailable={storageUnavailable} goals={activeGoals} />}
          {page === "settings" && <SettingsPage headingRef={heading} appearance={appearance} provider={provider} config={config} configError={configError} discoveryIssue={Boolean(mapData ? discoveryWarning || mapError : chatDiscoveryIssue || mapError)} onReconnect={() => setConfigRetry(n => n + 1)} browserKeyConfigured={Boolean(browserMapsKey)} preferences={preferences} onPreference={setPreference} />}


        </main>
      </div>
      <div className="action-notice" aria-live="polite" aria-atomic="true">{notice && <span>{notice.text}</span>}</div>

    </div></MealPlanContext.Provider></RatingsContext.Provider>
  );
}
