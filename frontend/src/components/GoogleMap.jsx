import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BranchPopup } from "./MapPopup.jsx";
import { mergeListings } from "./mapListings.js";
import RestaurantSheet, { useIsPhone } from "./RestaurantSheet.jsx";

const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
let mapsPromise;
function loadMaps() {
  if (!mapsPromise) {
    mapsPromise = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => fail("Google Maps took too long to load. Check your connection."), 15000);
      function fail(message) { clearTimeout(timeout); mapsPromise = null; reject(new Error(message)); }
      window.gm_authFailure = () => {
        const message = "Google Maps could not authenticate. Check the browser key and its allowed origins in Settings.";
        fail(message);
        window.dispatchEvent(new CustomEvent("calcipher:maps-auth-error", { detail: message }));
      };
      window.initCalCipherMap = () => { clearTimeout(timeout); resolve(window.google.maps); };
      const script = document.createElement("script");
      const params = new URLSearchParams({ key, callback: "initCalCipherMap", libraries: "marker", loading: "async" });
      script.src = "https://maps.googleapis.com/maps/api/js?" + params;
      script.onerror = () => fail("Google Maps could not load. Check your connection.");
      document.head.appendChild(script);
    });
  }
  return mapsPromise;
}

function circleIcon(maps, radius, fillColor) {
  return { path: maps.SymbolPath.CIRCLE, scale: radius, fillColor, fillOpacity: 1,
    strokeColor: "#FFFFFF", strokeWeight: 2 };
}

export default function GoogleMap({ location, onMove, onViewport, radiusKm, pins, results, focus, reserveBottom = false, goals, isFavorite, onToggleFavorite }) {
  const container = useRef(null);
  const initialCenter = useRef(location);
  const [map, setMap] = useState(null);
  const [popup, setPopup] = useState(null);
  const [error, setError] = useState(null);
  const phone = useIsPhone();
  const infoWindow = useRef(null);

  useEffect(() => {
    if (!key) return;
    let active = true;
    const authFailure = event => setError(event.detail);
    window.addEventListener("calcipher:maps-auth-error", authFailure);
    loadMaps().then((maps) => {
      if (active) setMap(new maps.Map(container.current, {
        center: initialCenter.current, zoom: 15,
        styles: ["poi", "transit.station"].map(featureType => ({
          featureType, elementType: "labels", stylers: [{ visibility: "off" }],
        })),
        // fullscreen sits top-right by default, where the Chat button lives
        clickableIcons: false, streetViewControl: false, mapTypeControl: false, fullscreenControl: false,
      }));
    }).catch((e) => active && setError(e.message));
    return () => { active = false; window.removeEventListener("calcipher:maps-auth-error", authFailure); };
  }, []);


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
      icon: circleIcon(maps, 9, "#475569"),
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
    const info = new maps.InfoWindow({ maxWidth: 320 });
    infoWindow.current = info;
    const closed = info.addListener("closeclick", () => setPopup(null));
    const listings = mergeListings(pins, results);
    const markers = Array.from(listings.values(), ({ pin, items }) => {
      const result = items[0];
      const color = result ? "#5B8219" : pin.has_calorie_data ? "#5B8219" : "#7B8B55";
      const marker = new maps.Marker({
        map, position: { lat: pin.latitude, lng: pin.longitude }, title: pin.poi_name,
        icon: circleIcon(maps, result ? 17 : 13, color),
        label: result ? { text: String(result.number), color: "#FFFFFF", fontWeight: "bold" } : null,
        zIndex: result ? 2 : pin.has_calorie_data ? 1 : 0,
      });
      marker.addListener("click", () => {
        if (phone) { // phones: bottom sheet instead of a cramped balloon
          info.close();
          setPopup({ sheet: true, pin, items });
          map.panTo(marker.getPosition());
          return;
        }
        const node = document.createElement("div");
        info.setContent(node);
        info.open({ map, anchor: marker });
        setPopup({ node, pin, items });
      });
      return marker;
    });
    return () => { closed.remove(); info.close(); infoWindow.current = null; setPopup(null); markers.forEach(marker => { marker.setMap(null); }); };
  }, [map, pins, results, phone]);

  // Google sizes a balloon when it opens, while our card is still empty, which made it too narrow
  // (the card then scrolled sideways). Re-set the content once rendered and whenever it grows.
  useEffect(() => {
    const info = infoWindow.current;
    if (!popup || popup.sheet || !info) return undefined;
    let last = "";
    const refit = () => {
      const size = `${popup.node.offsetWidth}x${popup.node.offsetHeight}`;
      if (size === last) return; // setContent re-lays out the node; don't loop on our own resize
      last = size;
      info.setContent(popup.node);
    };
    const frame = requestAnimationFrame(refit);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => requestAnimationFrame(refit)) : null;
    observer?.observe(popup.node);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [popup]);

  return <>
    {/* leave room for the collapsed inspector bar only while it is on screen */}
    <div ref={container} className={`w-full ${reserveBottom ? "h-[calc(100%-2.75rem)]" : "h-full"}`} />
    {(!key || error) && <div className="map-empty" role="status">
      <div className="empty-illustration"><img src="/brand/icon-avocado.png" alt="" width="56" height="56" /></div>
      <span className="empty-badge">{error ? "Map unavailable" : "Map setup needed"}</span>
      <h3>Restaurant map unavailable</h3>
      <p>{error || "Google Maps needs a browser key. Open Settings to check the connection."}</p>
      <a href="#/settings">Open Settings</a>
    </div>}
    {popup && (() => {
      const content = <>
        {popup.pin.has_calorie_data !== undefined ? <BranchPopup pin={popup.pin} goals={goals} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} /> : <strong>{popup.pin.poi_name}</strong>}
        {popup.pin.has_calorie_data === undefined && popup.items.length > 0 && <details className="popup-results"><summary>Matching meals ({popup.items.length})</summary>{popup.items.map(({item}) => <p key={item.item_id}>{item.food_item} · {item.calories} kcal</p>)}</details>}
        {(popup.pin.attributions || []).map(a => <p key={a.provider}>{a.provider}</p>)}
      </>;
      return popup.sheet
        ? <RestaurantSheet title={popup.pin.poi_name} onClose={() => setPopup(null)}>{content}</RestaurantSheet>
        : createPortal(content, popup.node);
    })()}
  </>;
}
