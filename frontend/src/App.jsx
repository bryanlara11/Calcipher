import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CalorieMap from "./components/CalorieMap.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import RagInspector from "./components/RagInspector.jsx";
import SplashScreen from "./components/SplashScreen.jsx";
import { chat, mapItems } from "./api/client.js";
import { BrandLogo, CloseButton, Icon } from "./components/Brand.jsx";
import ThemeControl, { useTheme } from "./components/ThemeControl.jsx";
import MealSearch from "./components/MealSearch.jsx";

const UP_MANILA = { lat: 14.5785, lng: 120.985 };
const EMPTY = [];
const formatRadius = meters => meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
const inPhilippines = ({ lat, lng }) => lat >= 4.5 && lat <= 21.5 && lng >= 116 && lng <= 127;

let nextId = 1;

export default function App() {
  const appearance = useTheme();
  const [location, setLocation] = useState(UP_MANILA);
  const [viewport, setViewport] = useState(null);
  const [searchViewport, setSearchViewport] = useState(null);
  const [retry, setRetry] = useState(0);
  const onViewport = useCallback(bounds => {
    setViewport(bounds);
    setSearchViewport(current => current ?? bounds);
  }, []);
  const [radiusMeters, setRadiusMeters] = useState(100);
  const [previewMeters, setPreviewMeters] = useState(100);
  const draggingRadius = useRef(false);
  const radiusKm = radiusMeters / 1000;
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [mapData, setMapData] = useState(null);
  const [mapError, setMapError] = useState(null);
  const [mapErrorDismissed, setMapErrorDismissed] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [showInspector, setShowInspector] = useState(false);
  const [messages, setMessages] = useState([]);
  const [lastChat, setLastChat] = useState(null);
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [geoError, setGeoError] = useState(null);
  useEffect(() => {
    function escape(event) {
      if (event.key !== "Escape") return;
      if (showInspector) { setShowInspector(false); setInspectorOpen(false); }
      else if (chatOpen) setChatOpen(false);
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [chatOpen, showInspector]);
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
    if (!searchViewport) return;
    const ctrl = new AbortController();
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      if (Date.now() < quotaUntilRef.current) return; // cooling down: don't even ask the backend
      mapItems({ ...location, radiusKm, viewport: searchViewport, includeUnmatched: true, signal: ctrl.signal })
        .then((d) => {
          setMapData(d);
          setMapError(null);
        })
        .catch((e) => {
          if (e.name === "AbortError") return;
          if (e.retryAfter && (e.code === "places_quota" || e.code === "places_unavailable" || e.status === 429)) {
            setQuotaUntil(Date.now() + e.retryAfter * 1000);
          }
          setMapError(e.message);
          setMapErrorDismissed(false); // a new failure shows again even if the last one was closed
        });
    }, 350);
    return () => {
      clearTimeout(debounce.current);
      ctrl.abort();
    };
  }, [location, radiusKm, searchViewport, retry]);

  const moveTo = useCallback((ll) => {
    const next = { lat: ll.lat, lng: ll.lng };
    if (!inPhilippines(next)) {
      setGeoError("CalCipher only covers the Philippines. Drop the pin inside the country.");
      return;
    }
    setGeoError(null);
    setLastChat(null);
    setMapData(null);
    setSearchViewport(viewport);
    setLocation(next);
  }, [viewport]);

  function applyRadius(meters) {
    if (meters === radiusMeters) return;
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
    setMessages((m) => [...m, { id: nextId++, role: "user", text }]);
    setBusy(true);
    try {
      const response = await chat({ message: text, ...location, radiusKm });
      setLastChat(response);
      setMessages((m) => [...m, { id: nextId++, role: "assistant", response }]);
      if (response.results[0]) setFocus({ lat: response.results[0].latitude, lng: response.results[0].longitude, t: Date.now() });
    } catch (e) {
      const title =
        e.code === "macro_physics_violation" ? "That combination isn't possible"
        : e.code === "rate_limited" ? "Too many searches"
        : e.code === "network" ? "Backend unreachable"
        : "Search failed";
      setMessages((m) => [...m, { id: nextId++, role: "error", title, text: e.message }]);
    } finally {
      setBusy(false);
    }
  }

  const alertText = geoError || (mapError && !mapErrorDismissed ? `Map data unavailable: ${mapError}` : null);
  function dismissAlert() {
    if (geoError) setGeoError(null);
    else setMapErrorDismissed(true);
  }

  return (
    <div className="app-shell">
      <SplashScreen />
      <a href="#meal-search" className="skip-link">Skip to meal search</a>
      <header className="app-header">
        <a className="brand-lockup" href="#meal-search" aria-label="CalCipher home">
          <BrandLogo decorative /><div><span className="brand-wordmark">CalCipher</span><span className="brand-tagline">A little insight. A better bite.</span></div>
        </a>
        <div className="header-center"><span className="nav-active"><Icon name="compass" size={17} /> Explore meals</span><span className="header-location"><Icon name="pin" size={15} /> Philippines</span></div>
        <div className="header-actions"><ThemeControl preference={appearance.preference} onChange={appearance.choose} />
          <button type="button" className={`button-chat ${chatOpen ? "is-active" : ""}`} aria-label={chatOpen ? "Hide chat" : "Meal assistant"} aria-expanded={chatOpen} aria-controls="chat-panel" onClick={() => setChatOpen(value => !value)}><Icon name="chat" size={18} /><span>{chatOpen ? "Hide chat" : "Meal assistant"}</span></button>
        </div>
      </header>
      <div className={`workspace ${chatOpen ? "chat-is-open" : ""}`}>
        <aside className="discovery-sidebar" aria-label="Meal search and map filters">
          <div className="sidebar-intro"><p className="eyebrow"><Icon name="leaf" size={15} /> GOOD FOOD, WITH INSIGHT</p><h1>Find your next<br /> <em>favorite meal.</em></h1><p>Nearby restaurants. Clearer nutrition.<br />A choice that works for you.</p></div>
          <div id="meal-search" tabIndex={-1}><MealSearch busy={busy} onSearch={query => { setChatOpen(true); send(query); }} /></div>
          <div className="map-filters">
            <div className="section-caption"><Icon name="pin" size={16} /><h2>Your neighborhood</h2></div>
            <div className="radius-heading"><label htmlFor="search-radius">Search radius</label><output htmlFor="search-radius">{formatRadius(previewMeters)}</output></div>
            <input id="search-radius" type="range" aria-label="Search radius" aria-valuetext={formatRadius(previewMeters)} min={100} max={5000} step={100} value={previewMeters}
              onPointerDown={event => { draggingRadius.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
              onChange={event => { const meters = event.currentTarget.valueAsNumber; setPreviewMeters(meters); if (!draggingRadius.current) applyRadius(meters); }}
              onLostPointerCapture={finishRadiusDrag} onBlur={finishRadiusDrag} />
            <div className="range-limits"><span>100 m</span><span>5 km</span></div>
            <div role="radiogroup" aria-label="Map pins" className="map-segments">{[[false, "All nearby"], [true, "With nutrition"]].map(([value, label]) =>
              <button key={label} type="button" role="radio" aria-checked={verifiedOnly === value} tabIndex={verifiedOnly === value ? 0 : -1}
                onClick={() => setVerifiedOnly(value)} onKeyDown={event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); setVerifiedOnly(!verifiedOnly); event.currentTarget.parentElement.querySelector(`[aria-checked="false"]`)?.focus(); } }}>
                {label}</button>)}</div>
            <button type="button" onClick={useMyLocation} className="location-button"><Icon name="compass" size={17} />Use my location</button>
          </div>
          <div className="source-note"><Icon name="check" size={18} /><p><strong>More clarity in every choice.</strong><br />See source checks, missing values, and prices before you decide.</p></div>
          <p className="sidebar-footer">Made for the way you eat.</p>
        </aside>
        <main className="map-workspace" aria-label="Restaurant discovery">
          <div className="map-heading"><div><p className="eyebrow">EXPLORE NEARBY</p><h2>Around your pin <span className="radius-badge">{formatRadius(radiusMeters)}</span></h2></div>
            {(viewport !== searchViewport || mapError) && <button type="button" disabled={quotaUntil > 0} className="button-secondary" onClick={() => { setSearchViewport(viewport); setRetry(n => n + 1); setMapError(null); }}><Icon name="search" size={16} />{viewport !== searchViewport ? "Search this area" : "Retry search"}</button>}
          </div>
          <div className="map-frame">
            <CalorieMap location={location} onMove={moveTo} onViewport={onViewport} radiusKm={previewMeters / 1000} pins={pins} results={lastChat?.results ?? EMPTY} focus={focus} theme={appearance.theme} />
            {alertText && <div role="alert" className="map-alert"><Icon name="info" size={18} /><p>{alertText}</p><CloseButton label="Dismiss message" onClick={dismissAlert} /></div>}
            <ul className="map-legend"><li><i className="legend-dot nutrition-dot" />Nutrition available</li>{!verifiedOnly && <li><i className="legend-dot other-dot" />No nutrition yet</li>}<li><i className="legend-dot location-dot" />Your pin</li></ul>
            {mapData && <p className="map-count">{pins.length} restaurant{pins.length === 1 ? "" : "s"} in this result</p>}
          </div>
          <div className="map-footnote"><Icon name="info" size={14} /><span>Nutrition is matched by brand. Availability and source prices can vary by branch.</span></div>
        </main>
        {chatOpen && <aside id="chat-panel" className="chat-drawer" aria-label="Meal assistant">
          <header className="chat-heading"><span className="assistant-mark"><Icon name="chat" size={21} /></span><div><h2>Your meal assistant</h2><p>Find a good fit, one bite at a time.</p></div><CloseButton label="Close chat" onClick={() => setChatOpen(false)} /></header>
          <div className="chat-context"><span><i className="status-dot" />Searching within {formatRadius(radiusMeters)}</span><button type="button" onClick={() => { setShowInspector(true); setInspectorOpen(true); }}>View retrieval details</button></div>
          <ChatPanel messages={messages} onSend={send} busy={busy} onLocate={item => setFocus({ lat: item.latitude, lng: item.longitude, t: Date.now() })} />
        </aside>}
      </div>
      {showInspector && <div className="inspector-layer"><RagInspector open={inspectorOpen} onToggle={() => setInspectorOpen(value => !value)} onClose={() => { setShowInspector(false); setInspectorOpen(false); }} chat={lastChat} map={mapData} /></div>}
    </div>
  );
}
