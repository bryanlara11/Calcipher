import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { brandItems } from "../api/client.js";

const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
let mapsPromise;
function loadMaps() {
  if (!mapsPromise) {
    mapsPromise = new Promise((resolve, reject) => {
      window.initCalCipherMap = () => resolve(window.google.maps);
      const script = document.createElement("script");
      const params = new URLSearchParams({ key, callback: "initCalCipherMap", libraries: "marker", loading: "async" });
      script.src = "https://maps.googleapis.com/maps/api/js?" + params;
      script.onerror = () => reject(new Error("Google Maps could not load. Check the connection and reload."));
      document.head.appendChild(script);
    });
  }
  return mapsPromise;
}

function circleIcon(maps, radius, fillColor) {
  return { path: maps.SymbolPath.CIRCLE, scale: radius, fillColor, fillOpacity: 1,
    strokeColor: "#FDEDC9", strokeWeight: 2 };
}

function BrandMenu({ slug }) {
  const [state, setState] = useState({ loading: true, items: [], error: null });
  useEffect(() => {
    const ctrl = new AbortController();
    brandItems(slug, { signal: ctrl.signal })
      .then((r) => setState({ loading: false, items: r.items, error: null }))
      .catch((e) => e.name !== "AbortError" && setState({ loading: false, items: [], error: e.message }));
    return () => ctrl.abort();
  }, [slug]);

  if (state.loading) return <p className="text-sm text-slate-mute">Loading menu…</p>;
  if (state.error) return <p className="text-sm text-warn">{state.error}</p>;
  return (
    <ul className="mt-2 max-h-48 overflow-y-auto pr-1 text-sm">
      {state.items.map((it) => (
        <li key={it.item_id} className="flex items-baseline justify-between gap-3 border-t border-slate-line py-1">
          <span>{it.food_item}<span className="block text-xs text-slate-mute">
            {it.serving_size} · {it.is_verified ? "Checked against source" : "Unverified"}
            {it.price_php == null ? " · Price unavailable" : ` · Source price ₱${it.price_php.toFixed(2)}`}
          </span><span className="block text-xs text-slate-mute">
            {[["Protein", it.protein_g], ["Carbs", it.carbs_g], ["Fat", it.fat_g]]
              .map(([name, value]) => `${name} ${value == null ? "unavailable" : `${value} g`}`).join(" · ")}
          </span></span>
          <span className="num type-cond whitespace-nowrap font-bold">{it.calories} kcal</span>
        </li>
      ))}
    </ul>
  );
}

function BranchPopup({ pin }) {
  return (
    <div className="w-56">
      <p className="text-base font-bold leading-tight">{pin.poi_name}</p>
      <p className="text-xs text-slate-mute">
        {pin.distance_km.toFixed(2)} km away{pin.address_hint ? `, ${pin.address_hint}` : ""}
      </p>
      {pin.has_calorie_data ? (
        <>
          <p className="mt-2 text-sm">
            {pin.item_count} {pin.brand_name} item{pin.item_count === 1 ? "" : "s"}, from{" "}
            <strong className="num">{pin.min_calories} kcal</strong>
          </p>
          <BrandMenu slug={pin.matched_brand_slug} />
        </>
      ) : (
        <p className="mt-2 text-sm text-slate-mute">
          No calorie data yet for this restaurant. It was found on Google Maps but isn't in the
          @anongcaloriesnito catalog.
        </p>
      )}
    </div>
  );
}

export default function CalorieMap({ location, onMove, onViewport, radiusKm, pins, results, focus, theme = "light", reserveBottom = false }) {
  const container = useRef(null);
  const initialCenter = useRef(location);
  const [map, setMap] = useState(null);
  const [popup, setPopup] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!key) return;
    let active = true;
    loadMaps().then((maps) => {
      if (active) setMap(new maps.Map(container.current, {
        center: initialCenter.current, zoom: 16,
        styles: ["poi", "transit.station"].map(featureType => ({
          featureType, elementType: "labels", stylers: [{ visibility: "off" }],
        })),
        // fullscreen sits top-right by default, where the Chat button lives
        clickableIcons: false, streetViewControl: false, mapTypeControl: false, fullscreenControl: false,
      }));
    }).catch((e) => active && setError(e.message));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!map) return;
    map.setOptions({ styles: [
      ...["poi", "transit.station"].map(featureType => ({ featureType, elementType: "labels", stylers: [{ visibility: "off" }] })),
      ...(theme === "dark" ? [
        { elementType: "geometry", stylers: [{ color: "#173A2B" }] },
        { elementType: "labels.text.fill", stylers: [{ color: "#D5DEC2" }] },
        { elementType: "labels.text.stroke", stylers: [{ color: "#102A20" }] },
        { featureType: "road", elementType: "geometry", stylers: [{ color: "#2F513C" }] },
        { featureType: "water", elementType: "geometry", stylers: [{ color: "#0B2724" }] },
      ] : []),
    ] });
  }, [map, theme]);

  useEffect(() => {
    if (!map) return;
    function reportBounds() {
      const bounds = map.getBounds()?.toJSON();
      if (bounds) onViewport([bounds.south, bounds.north, bounds.west, bounds.east]
        .map(value => value.toFixed(5)).join(","));
    }
    const idle = map.addListener("idle", reportBounds);
    reportBounds();
    return () => idle.remove();
  }, [map, onViewport]);

  useEffect(() => {
    if (!map) return;
    const maps = window.google.maps;
    const marker = new maps.Marker({
      map, position: location, draggable: true, title: "Your search location", zIndex: 3,
      icon: circleIcon(maps, 9, "#F7C15A"),
    });
    const circle = new maps.Circle({ map, center: location, radius: radiusKm * 1000,
      clickable: false, strokeWeight: 1, fillOpacity: 0.05 });
    const move = (e) => e.latLng && onMove(e.latLng.toJSON());
    const click = map.addListener("click", move);
    const drag = marker.addListener("dragend", move);
    return () => { click.remove(); drag.remove(); marker.setMap(null); circle.setMap(null); };
  }, [map, location, onMove, radiusKm]);

  useEffect(() => {
    if (!map || !focus) return;
    map.panTo({ lat: focus.lat, lng: focus.lng });
    map.setZoom(Math.max(map.getZoom(), 16));
  }, [map, focus]);

  useEffect(() => {
    if (!map) return;
    const maps = window.google.maps;
    const info = new maps.InfoWindow();
    const listings = new Map(pins.map(pin => [pin.place_id, { pin, items: [] }]));
    results.forEach((item, index) => {
      if (!listings.has(item.place_id)) listings.set(item.place_id, { pin: item, items: [] });
      listings.get(item.place_id).items.push({ item, number: index + 1 });
    });
    const markers = Array.from(listings.values(), ({ pin, items }) => {
      const result = items[0];
      const color = result ? "#F7C15A" : pin.has_calorie_data ? "#064D2E" : "#687D6C";
      const marker = new maps.Marker({
        map, position: { lat: pin.latitude, lng: pin.longitude }, title: pin.poi_name,
        icon: circleIcon(maps, result ? 17 : 13, color),
        label: result ? { text: String(result.number), color: "#064D2E", fontWeight: "bold" } : null,
        zIndex: result ? 2 : pin.has_calorie_data ? 1 : 0,
      });
      marker.addListener("click", () => {
        const node = document.createElement("div");
        info.setContent(node);
        info.open({ map, anchor: marker });
        setPopup({ node, pin, items });
      });
      return marker;
    });
    return () => { info.close(); setPopup(null); markers.forEach(marker => { marker.setMap(null); }); };
  }, [map, pins, results]);

  return <>
    {/* leave room for the collapsed inspector bar only while it is on screen */}
    <div ref={container} className={`w-full ${reserveBottom ? "h-[calc(100%-2.75rem)]" : "h-full"}`} />
    {(!key || error) && <div className="map-empty" role="status">
      <div className="empty-illustration"><img src="/brand/icon.svg" alt="" width="56" height="56" /></div>
      <span className="empty-badge">{error ? "MAP UNAVAILABLE" : "MAP NOT CONFIGURED"}</span>
      <h3>Your next meal is out there.</h3>
      <p>{error || "The restaurant map needs a connection to Google Maps. Your meal filters and assistant are ready when it is configured."}</p>
      <details><summary>Map setup details</summary><p>Set VITE_GOOGLE_MAPS_API_KEY in frontend/.env and restart Vite. Restaurant discovery also requires the server Places key.</p></details>
    </div>}
    {popup && createPortal(<>
      {popup.pin.has_calorie_data !== undefined ? <BranchPopup pin={popup.pin} /> : <strong>{popup.pin.poi_name}</strong>}
      {popup.items.map(({item, number}) => <p key={item.item_id}>{number}. {item.food_item} · {item.calories} kcal</p>)}
      {(popup.pin.attributions || []).map(a => <p key={a.provider}>{a.provider}</p>)}
    </>, popup.node)}
  </>;
}
