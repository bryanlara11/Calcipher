import { useCallback, useEffect, useRef, useState } from "react";
import CalorieMap from "./components/CalorieMap.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import RagInspector from "./components/RagInspector.jsx";
import { chat, mapItems } from "./api/client.js";

const UP_MANILA = { lat: 14.5785, lng: 120.985 };
const RADII = [1, 2, 3, 5];
const inPhilippines = ({ lat, lng }) => lat >= 4.5 && lat <= 21.5 && lng >= 116 && lng <= 127;

let nextId = 1;

export default function App() {
  const [location, setLocation] = useState(UP_MANILA);
  const [radiusKm, setRadiusKm] = useState(2);
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [mapData, setMapData] = useState(null);
  const [mapError, setMapError] = useState(null);
  const [messages, setMessages] = useState([]);
  const [lastChat, setLastChat] = useState(null);
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [geoError, setGeoError] = useState(null);
  const debounce = useRef();

  useEffect(() => {
    const ctrl = new AbortController();
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      mapItems({ ...location, radiusKm, includeUnmatched: !verifiedOnly, signal: ctrl.signal })
        .then((d) => {
          setMapData(d);
          setMapError(null);
        })
        .catch((e) => e.name !== "AbortError" && setMapError(e.message));
    }, 350);
    return () => {
      clearTimeout(debounce.current);
      ctrl.abort();
    };
  }, [location, radiusKm, verifiedOnly]);

  const moveTo = useCallback((ll) => {
    const next = { lat: ll.lat, lng: ll.lng };
    if (!inPhilippines(next)) {
      setGeoError("CalCipher only covers the Philippines. Drop the pin inside the country.");
      return;
    }
    setGeoError(null);
    setLocation(next);
  }, []);

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

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <main className="relative h-[48vh] shrink-0 overflow-hidden lg:h-full lg:flex-1">
        <CalorieMap
          location={location}
          onMove={moveTo}
          radiusKm={radiusKm}
          pins={mapData?.branches ?? []}
          results={lastChat?.results ?? []}
          focus={focus}
        />

        <div className="pointer-events-none absolute inset-x-0 top-0 z-[1000] flex flex-wrap items-start gap-2 p-3">
          <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-md border-2 border-ink bg-label p-1.5 shadow-[3px_3px_0_#18202B]">
            <div role="radiogroup" aria-label="Map pins" className="flex rounded border border-ink">
              {[
                [true, "Calorie spots"],
                [false, "All restaurants"],
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
              <select
                value={radiusKm}
                onChange={(e) => setRadiusKm(Number(e.target.value))}
                className="rounded border border-ink bg-label px-1 py-0.5"
              >
                {RADII.map((r) => (
                  <option key={r} value={r}>
                    {r} km
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={useMyLocation} className="rounded px-2 py-1 text-sm font-semibold text-ube underline decoration-2 underline-offset-2">
              Use my location
            </button>
          </div>
          {(geoError || mapError) && (
            <p role="alert" className="pointer-events-auto max-w-sm rounded-md border-2 border-warn bg-label px-3 py-1.5 text-sm">
              {geoError || `Map data unavailable: ${mapError}`}
            </p>
          )}
        </div>

        <ul className="pointer-events-none absolute bottom-14 left-3 z-[999] space-y-1 rounded-md bg-label/95 px-3 py-2 text-xs shadow">
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-ink bg-ube" /> Has calorie data</li>
          {!verifiedOnly && <li className="flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 rounded-full bg-nodata" /> No data yet</li>}
          <li className="flex items-center gap-2"><span className="inline-block h-3 w-3 rounded-full border border-ink bg-mango" /> Your pin (drag or click map)</li>
        </ul>

        <RagInspector open={inspectorOpen} onToggle={() => setInspectorOpen((o) => !o)} chat={lastChat} map={mapData} />
      </main>

      <aside className="flex min-h-0 flex-1 flex-col border-ink bg-slate-map lg:w-[27rem] lg:flex-none lg:border-l-2">
        <header className="border-b-2 border-ink bg-label px-4 py-3">
          <h1 className="type-label text-3xl leading-none">CalCipher</h1>
          <p className="mt-1 text-sm text-slate-mute">
            Calorie-counted meals at restaurants within {radiusKm} km of your pin.
          </p>
        </header>
        <ChatPanel messages={messages} onSend={send} busy={busy} onLocate={(it) => setFocus({ lat: it.latitude, lng: it.longitude, t: Date.now() })} />
      </aside>
    </div>
  );
}
