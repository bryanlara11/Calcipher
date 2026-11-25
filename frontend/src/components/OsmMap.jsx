import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { BranchPopup } from "./MapPopup.jsx";
import { mergeListings } from "./mapListings.js";

export default function OsmMap({ location, onMove, onViewport, radiusKm, pins, results, focus, goals, isFavorite, onToggleFavorite }) {
  const container = useRef(null);
  const liveMap = useRef(null);
  const initialCenter = useRef(location);
  const tiles = useRef(null);
  const [map, setMap] = useState(null);
  const [popup, setPopup] = useState(null);
  const [tileError, setTileError] = useState(false);
  useEffect(() => {
    const instance = L.map(container.current, { scrollWheelZoom: false, minZoom: 6, maxZoom: 19 })
      .setView(initialCenter.current, 15);
    tiles.current = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>',
    }).on("tileerror", () => setTileError(true)).addTo(instance);
    liveMap.current = instance;
    setMap(instance);
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => {
      if (liveMap.current === instance && container.current?.clientWidth && container.current?.clientHeight) instance.invalidateSize();
    }) : null;
    observer?.observe(container.current);
    return () => { observer?.disconnect(); liveMap.current = null; instance.remove(); };
  }, []);
  useEffect(() => {
    if (!map || liveMap.current !== map) return;
    const report = () => {
      if (liveMap.current !== map || !container.current?.clientWidth || !container.current?.clientHeight) return;
      const b = map.getBounds();
      onViewport([b.getSouth(), b.getNorth(), b.getWest(), b.getEast()].map(n => n.toFixed(5)).join(","));
    };
    report(); map.on("moveend", report);
    return () => { map.off("moveend", report); };
  }, [map, onViewport]);
  useEffect(() => {
    if (!map || liveMap.current !== map) return;
    const icon = L.divIcon({ className: "search-pin", html: '<span></span>', iconSize: [24, 24], iconAnchor: [12, 12] });
    const marker = L.marker(location, { icon, draggable: true, title: "Your search location. Drag to move.", zIndexOffset: 1000 }).addTo(map);
    const circle = L.circle(location, { radius: radiusKm * 1000, color: "#58666C", weight: 2, fillColor: "#64748B", fillOpacity: .06, interactive: false }).addTo(map);
    marker.on("dragend", () => { const p = marker.getLatLng(); onMove({ lat: p.lat, lng: p.lng }); });
    const click = event => onMove({ lat: event.latlng.lat, lng: event.latlng.lng });
    map.on("click", click);
    return () => { map.off("click", click); marker.remove(); circle.remove(); };
  }, [map, location, radiusKm, onMove]);
  useEffect(() => {
    if (map && liveMap.current === map && focus) map.setView([focus.lat, focus.lng], Math.max(map.getZoom(), 16));
  }, [map, focus]);
  useEffect(() => {
    if (!map || liveMap.current !== map) return;
    const layer = L.layerGroup().addTo(map);
    const close = () => setPopup(null);
    map.on("popupclose", close);
    mergeListings(pins, results).forEach(({ pin, items }) => {
      const number = items[0]?.number;
      const icon = L.divIcon({
        className: `restaurant-pin ${number ? "result-pin" : pin.has_calorie_data ? "has-nutrition" : "no-nutrition"}`,
        html: `<span>${number ?? ""}</span>`, iconSize: [30, 30], iconAnchor: [15, 15],
      });
      const node = document.createElement("div");
      const marker = L.marker([pin.latitude, pin.longitude], { icon, title: pin.poi_name, bubblingMouseEvents: false }).addTo(layer);
      marker.bindPopup(node, { maxWidth: 300, minWidth: 200, autoPanPadding: [16, 16] });
      marker.on("popupopen", () => setPopup({ node, pin, items, instance: marker.getPopup() }));
    });
    return () => { map.off("popupclose", close); layer.remove(); setPopup(null); };
  }, [map, pins, results]);
  useEffect(() => {
    if (!popup) return;
    const update = () => { if (liveMap.current && popup.instance.isOpen()) popup.instance.update(); };
    update();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    observer?.observe(popup.node);
    return () => observer?.disconnect();
  }, [popup]);
  return <>
    <div ref={container} className="osm-map" role="region" aria-label="Restaurant map. Use arrow keys to pan, plus and minus to zoom." />
    {tileError && <div className="tile-error" role="status">Some map tiles could not load. <button type="button" onClick={() => { setTileError(false); tiles.current?.redraw(); }}>Retry map</button></div>}
    {popup && createPortal(<>
      {popup.pin.has_calorie_data !== undefined ? <BranchPopup pin={popup.pin} goals={goals} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} /> : <strong>{popup.pin.poi_name}</strong>}
      {popup.pin.has_calorie_data === undefined && popup.items.length > 0 && <details className="popup-results"><summary>Matching meals ({popup.items.length})</summary>{popup.items.map(({ item }) => <p key={item.item_id}>{item.food_item} · {item.calories} kcal</p>)}</details>}
    </>, popup.node)}
  </>;
}
