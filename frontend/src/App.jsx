import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CalorieMap from "./components/CalorieMap.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import RagInspector from "./components/RagInspector.jsx";
import SplashScreen from "./components/SplashScreen.jsx";
import { chat, mapItems } from "./api/client.js";

const UP_MANILA = { lat: 14.5785, lng: 120.985 };
const EMPTY = [];
const formatRadius = meters => meters < 1000 ? `${meters} m` : `${meters / 1000} km`;
const inPhilippines = ({ lat, lng }) => lat >= 4.5 && lat <= 21.5 && lng >= 116 && lng <= 127;

let nextId = 1;

function CloseButton({ label, onClick, className = "" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`grid h-7 w-7 shrink-0 place-items-center rounded text-lg leading-none text-ink hover:bg-slate-map ${className}`}
    >
      <span aria-hidden="true">×</span>
    </button>
  );
}

function ChatIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor">
      <path d="M3 4.5A2.5 2.5 0 0 1 5.5 2h9A2.5 2.5 0 0 1 17 4.5v6a2.5 2.5 0 0 1-2.5 2.5H9l-4 3.5V13h.5-1A2.5 2.5 0 0 1 3 10.5z" />
    </svg>
  );
}

export default function App() {
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
  const [showInspector, setShowInspector] = useState(true);
  const [messages, setMessages] = useState([]);
  const [lastChat, setLastChat] = useState(null);
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [geoError, setGeoError] = useState(null);
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
    <div className="flex h-full flex-col lg:flex-row">
      <SplashScreen />
      <main
        className={`relative shrink-0 overflow-hidden lg:h-full lg:flex-1 ${chatOpen ? "h-[48vh]" : "h-full"}`}
      >
        <CalorieMap
          location={location}
          onMove={moveTo}
          onViewport={onViewport}
          radiusKm={previewMeters / 1000}
          pins={pins}
          results={lastChat?.results ?? EMPTY}
          focus={focus}
          reserveBottom={chatOpen && showInspector}
        />

        <button
          type="button"
          onClick={() => setChatOpen((o) => !o)}
          aria-expanded={chatOpen}
          aria-controls="chat-panel"
          className={`absolute right-3 top-3 z-[1001] flex items-center gap-1.5 rounded-md border-2 border-ink px-3 py-1.5 text-sm font-bold shadow-[3px_3px_0_#18202B] ${
            chatOpen ? "bg-label text-ink hover:bg-slate-map" : "bg-ube text-label hover:bg-ink"
          }`}
        >
          <ChatIcon />
          {chatOpen ? "Hide chat" : "Chat"}
        </button>

        {/* pr-28 keeps the wrapping controls clear of the Chat button */}
        <div className="pointer-events-none absolute inset-x-0 top-0 z-[1000] flex flex-wrap items-start gap-2 p-3 pr-28">
          <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-md border-2 border-ink bg-label p-1.5 shadow-[3px_3px_0_#18202B]">
            <div role="radiogroup" aria-label="Map pins" className="flex rounded border border-ink">
              {[
                [true, "Calorie spots"],
                [false, "Nearby restaurants"],
              ].map(([val, label]) => (
                <button
                  key={label}
                  role="radio"
                  aria-checked={verifiedOnly === val}
                  onClick={() => setVerifiedOnly(val)}
                  className={`px-2.5 py-1 text-sm font-semibold ${verifiedOnly === val ? "bg-ink text-label" : "hover:bg-slate-map"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 px-1 text-sm font-semibold">
              Radius
              <input
                type="range"
                aria-label="Search radius"
                aria-valuetext={formatRadius(previewMeters)}
                min={100}
                max={5000}
                step={100}
                value={previewMeters}
                onPointerDown={event => {
                  draggingRadius.current = true;
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onChange={event => {
                  const meters = event.currentTarget.valueAsNumber;
                  setPreviewMeters(meters);
                  if (!draggingRadius.current) applyRadius(meters);
                }}
                onLostPointerCapture={finishRadiusDrag}
                onBlur={finishRadiusDrag}
                className="w-28 cursor-pointer accent-ube"
              />
              <span className="num min-w-12">{formatRadius(previewMeters)}</span>
            </label>
            {(viewport !== searchViewport || mapError) && (
              <button type="button" disabled={quotaUntil > 0}
                title={quotaUntil > 0 ? "Google Places is cooling down; try again shortly" : undefined}
                className="rounded px-2 py-1 text-sm font-semibold text-ube underline disabled:cursor-not-allowed disabled:opacity-40"
                onClick={() => { setSearchViewport(viewport); setRetry(n => n + 1); setMapError(null); }}>
                {viewport !== searchViewport ? "Search this area" : "Retry search"}
              </button>
            )}
            <button type="button" onClick={useMyLocation} className="rounded px-2 py-1 text-sm font-semibold text-ube underline decoration-2 underline-offset-2">
              Use my location
            </button>
          </div>
          {alertText && (
            <div role="alert" className="pointer-events-auto flex max-w-sm items-start gap-1 rounded-md border-2 border-warn bg-label py-1 pl-3 pr-1 text-sm">
              <p className="flex-1 py-0.5">{alertText}</p>
              <CloseButton label="Dismiss message" onClick={dismissAlert} />
            </div>
          )}
        </div>

        <ul
          className={`pointer-events-none absolute left-3 z-[999] space-y-1 rounded-md bg-label/95 px-3 py-2 text-xs shadow ${
            chatOpen && showInspector ? "bottom-14" : "bottom-8"
          }`}
        >
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-ink bg-ube" /> Has calorie data</li>
          {!verifiedOnly && <li className="flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 rounded-full bg-nodata" /> No data yet</li>}
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-ink bg-mango" /> Your pin (drag or click map)</li>
        </ul>

        {chatOpen && showInspector && (
          <RagInspector
            open={inspectorOpen}
            onToggle={() => setInspectorOpen((o) => !o)}
            onClose={() => { setShowInspector(false); setInspectorOpen(false); }}
            chat={lastChat}
            map={mapData}
          />
        )}
      </main>

      {chatOpen && (
        <aside id="chat-panel" className="flex min-h-0 flex-1 flex-col border-ink bg-slate-map lg:w-[27rem] lg:flex-none lg:border-l-2">
          <header className="flex items-start gap-2 border-b-2 border-ink bg-label px-4 py-3">
            <div className="min-w-0 flex-1">
              <h1 className="type-label text-3xl leading-none">CalCipher</h1>
              <p className="mt-1 text-sm text-slate-mute">
                Calorie-counted meals at restaurants within {formatRadius(radiusMeters)} of your pin.
              </p>
              {!showInspector && (
                <button type="button" onClick={() => setShowInspector(true)}
                  className="mt-1 text-sm font-semibold text-ube underline decoration-2 underline-offset-2">
                  Show retrieval inspector
                </button>
              )}
            </div>
            <CloseButton label="Close chat" onClick={() => setChatOpen(false)} className="-mr-2 -mt-1" />
          </header>
          <ChatPanel messages={messages} onSend={send} busy={busy} onLocate={(it) => setFocus({ lat: it.latitude, lng: it.longitude, t: Date.now() })} />
        </aside>
      )}
    </div>
  );
}
