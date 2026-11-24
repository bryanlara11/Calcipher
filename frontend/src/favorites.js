import { useCallback, useEffect, useState } from "react";
import { branchKey } from "./components/mapListings.js";

export const FAVORITES_KEY = "calcipher.favorites.v1";

function savedPlace(pin) {
  if (!pin || typeof pin.poi_name !== "string" || !pin.poi_name.trim()
    || !Number.isFinite(pin.latitude) || !Number.isFinite(pin.longitude)
    || pin.latitude < 4.5 || pin.latitude > 21.5 || pin.longitude < 116 || pin.longitude > 127
    || !(pin.place_id || pin.osm_type && Number.isInteger(pin.osm_id))) return null;
  return {
    poi_name: pin.poi_name.slice(0, 255), latitude: pin.latitude, longitude: pin.longitude,
    place_id: pin.place_id || null, osm_type: pin.osm_type || null, osm_id: pin.osm_id ?? null,
    address_hint: pin.address_hint || null, amenity_type: pin.amenity_type || null,
    brand_name: pin.brand_name || null, matched_brand_slug: pin.matched_brand_slug || pin.brand_slug || null,
    has_catalog_data: Boolean(pin.has_catalog_data || pin.matched_brand_slug || pin.brand_slug),
    has_calorie_data: Boolean(pin.has_calorie_data || pin.is_verified),
  };
}

function parse(value) {
  try {
    const rows = JSON.parse(value || "[]");
    if (!Array.isArray(rows)) return [];
    return [...new Map(rows.map(savedPlace).filter(Boolean).map(pin => [branchKey(pin), pin])).values()];
  } catch { return []; }
}

export function useFavorites() {
  const [favorites, setFavorites] = useState(() => {
    try { return parse(localStorage.getItem(FAVORITES_KEY)); } catch { return []; }
  });
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  useEffect(() => {
    try { localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites)); setStorageUnavailable(false); }
    catch { setStorageUnavailable(true); }
  }, [favorites]);
  useEffect(() => {
    const sync = event => { if (event.key === FAVORITES_KEY || event.key === null) setFavorites(parse(event.newValue)); };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  const isFavorite = useCallback(pin => favorites.some(item => branchKey(item) === branchKey(pin)), [favorites]);
  const toggleFavorite = useCallback(pin => {
    const value = savedPlace(pin);
    if (!value) return;
    setFavorites(current => current.some(item => branchKey(item) === branchKey(value))
      ? current.filter(item => branchKey(item) !== branchKey(value)) : [...current, value]);
  }, []);
  return { favorites, isFavorite, toggleFavorite, storageUnavailable };
}
