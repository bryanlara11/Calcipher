// Thin fetch wrapper around the FastAPI backend. In dev, Vite proxies /api -> :8000.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(status, detail, code, retryAfter = null) {
    super(detail);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter; // seconds, from the Retry-After header when the API sends one
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "Can't reach the CalCipher API. Check that the backend is running on port 8000.", "network");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const retryAfter = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
    throw new ApiError(res.status, body.detail ?? `Request failed (${res.status})`, body.code ?? "http_error",
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null);
  }
  return body;
}

export function chat({ message, lat, lng, radiusKm, signal, provider, filters, previousMessage }) {
  return request("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify({ message, lat, lng, radius_km: radiusKm, ...(filters ? { filters } : {}), ...(previousMessage ? { previous_message: previousMessage } : {}) }),
    signal,
    headers: { "Content-Type": "application/json", "X-CalCipher-Map-Provider": provider ?? "osm" },
  });
}

// Coordinates go in a POST body, not the URL, so they stay out of proxy and access logs.
// deviceLocation marks a pin from the browser's location; the server then writes nothing.
export function mapItems({ lat, lng, radiusKm, viewport, includeUnmatched, includePending = false, deviceLocation = false, signal, provider }) {
  const body = {
    lat: Number(lat.toFixed(6)),
    lng: Number(lng.toFixed(6)),
    radius_km: radiusKm,
    include_unmatched: Boolean(includeUnmatched),
    include_pending: Boolean(includePending),
    device_location: Boolean(deviceLocation),
    ...(viewport ? { viewport: viewport.split(",").map(Number) } : {}),
  };
  return request("/api/v1/map-items", {
    method: "POST", body: JSON.stringify(body), signal,
    headers: { "Content-Type": "application/json", "X-CalCipher-Map-Provider": provider ?? "osm" },
  });
}

export const browserMapsKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY?.trim() ?? "";
export function mapConfig({ signal } = {}) {
  return request(`/api/v1/map-config?browser_key_configured=${Boolean(browserMapsKey)}`, { signal });
}

export function brandItems(slug, { signal, includePending = false } = {}) {
  return request(`/api/v1/brands/${encodeURIComponent(slug)}/items?include_pending=${includePending}`, { signal });
}

export function health() {
  return request("/api/v1/health");
}

export function catalogSummary({ signal } = {}) {
  return request("/api/v1/catalog-summary", { signal });
}

export function catalogBrands({ signal } = {}) {
  return request("/api/v1/catalog-brands", { signal });
}

export function rateMeal(itemId, voterId, score) {
  return request(`/api/v1/meal-ratings/${encodeURIComponent(itemId)}`, { method: "POST", body: JSON.stringify({ voter_id: voterId, score }) });
}
