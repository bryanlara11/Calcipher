import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { brandItems } from "../api/client.js";

const youIcon = L.divIcon({ className: "", html: '<div class="pin-you"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });

function resultIcon(n) {
  return L.divIcon({
    className: "",
    html: `<div class="pin-result"><span>${n}</span></div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    popupAnchor: [0, -24],
  });
}

function ClickToMove({ onMove }) {
  useMapEvents({ click: (e) => onMove({ lat: e.latlng.lat, lng: e.latlng.lng }) });
  return null;
}

function FlyTo({ target }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo([target.lat, target.lng], Math.max(map.getZoom(), 16), { duration: 0.6 });
  }, [target, map]);
  return null;
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
          <span>{it.food_item}</span>
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
          No calorie data yet for this restaurant. It was found on OpenStreetMap but isn't in the
          @anongcaloriesnito catalog.
        </p>
      )}
    </div>
  );
}

export default function CalorieMap({ location, onMove, radiusKm, pins, results, focus }) {
  const center = useMemo(() => [location.lat, location.lng], []); // initial view only
  const resultKeys = useMemo(() => new Set(results.map((r) => `${r.osm_type}/${r.osm_id}`)), [results]);

  return (
    <MapContainer center={center} zoom={15} className="h-full w-full" zoomControl={false} attributionControl>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        maxZoom={19}
      />
      <ClickToMove onMove={onMove} />
      <FlyTo target={focus} />
      <Circle
        center={[location.lat, location.lng]}
        radius={radiusKm * 1000}
        pathOptions={{ color: "#18202B", weight: 1.5, dashArray: "6 6", fillColor: "#F2A900", fillOpacity: 0.05 }}
        interactive={false}
      />

      {pins
        .filter((p) => !resultKeys.has(`${p.osm_type}/${p.osm_id}`))
        .map((p) => (
          <CircleMarker
            key={`${p.osm_type}/${p.osm_id}`}
            center={[p.latitude, p.longitude]}
            radius={p.has_calorie_data ? 8 : 5}
            pathOptions={
              p.has_calorie_data
                ? { color: "#18202B", weight: 1.5, fillColor: "#5B2A86", fillOpacity: 0.95 }
                : { color: "#7D8792", weight: 1, fillColor: "#A3ABB4", fillOpacity: 0.7 }
            }
          >
            <Popup>
              <BranchPopup pin={p} />
            </Popup>
          </CircleMarker>
        ))}

      {results.map((r, i) => (
        <Marker key={`r-${r.osm_type}/${r.osm_id}/${r.item_id}`} position={[r.latitude, r.longitude]} icon={resultIcon(i + 1)}>
          <Popup>
            <div className="w-52">
              <p className="text-base font-bold leading-tight">{r.food_item}</p>
              <p className="text-sm">{r.poi_name}</p>
              <p className="num mt-1 text-sm">
                <strong>{r.calories} kcal</strong> · {r.distance_km.toFixed(2)} km
              </p>
            </div>
          </Popup>
        </Marker>
      ))}

      <Marker
        position={[location.lat, location.lng]}
        icon={youIcon}
        draggable
        eventHandlers={{ dragend: (e) => onMove(e.target.getLatLng()) }}
        keyboard={false}
        title="Your search location (drag to move)"
      />
    </MapContainer>
  );
}
