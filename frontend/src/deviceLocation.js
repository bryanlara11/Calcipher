// One-shot device location for Nearby. Nothing here is stored: no browser storage, no cookies,
// and the caller keeps the reading only in component memory.

export const LOCATION_TIMEOUT_MS = 10000;

// Round to what the selected radius needs: ~110 m steps for 1 km and wider, ~11 m below that.
export function coarseLocation({ latitude, longitude }, radiusMeters) {
  const factor = radiusMeters >= 1000 ? 1e3 : 1e4;
  return { lat: Math.round(latitude * factor) / factor, lng: Math.round(longitude * factor) / factor };
}

// The search box a fresh pin needs, in the map's "south,north,west,east" viewport format.
export function radiusViewport({ lat, lng }, radiusMeters) {
  const dLat = radiusMeters / 111320;
  const dLng = radiusMeters / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.01));
  return [lat - dLat, lat + dLat, lng - dLng, lng + dLng].map(n => n.toFixed(5)).join(",");
}

export function locationErrorMessage(code, mapsEnabled) {
  const reason = code === "unsupported" ? "This browser can’t share its location."
    : code === "outside" ? "Your location is outside the Philippines, which CalCipher covers."
    : code === 1 ? "Location access is blocked for this site."
    : code === 3 ? "Finding your location took too long."
    : "Your device couldn’t find its location.";
  const next = mapsEnabled ? "Your pin hasn’t moved. Tap the map or drag the pin to choose a spot."
    : "Your pin hasn’t moved. Turn on maps in Settings to choose a spot.";
  return `${reason} ${next}`;
}

// Ordinary accuracy, one reading, finite timeout. Never watchPosition.
export function readDeviceLocation(onSuccess, onError) {
  if (!globalThis.navigator?.geolocation) { onError("unsupported"); return; }
  navigator.geolocation.getCurrentPosition(
    position => onSuccess(position.coords),
    error => onError(error?.code ?? 2),
    { enableHighAccuracy: false, timeout: LOCATION_TIMEOUT_MS, maximumAge: 60000 },
  );
}

// `provider` is the map in use; `googleFallback` is true when the server may ask Google Places for
// restaurants while OpenStreetMap is unavailable.
export function locationNote(provider, googleFallback = false) {
  const service = provider === "google" ? "Google Maps" : "OpenStreetMap";
  const fallback = provider !== "google" && googleFallback ? " If OpenStreetMap is down, Google Places may be asked instead." : "";
  return `Reads your device’s location once, only when you tap. CalCipher’s server and ${service} receive an approximate spot to find restaurants and show the map.${fallback}`;
}
