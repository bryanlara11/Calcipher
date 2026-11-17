// Thin fetch wrapper around the FastAPI backend. In dev, Vite proxies /api -> :8000.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(status, detail, code) {
    super(detail);
    this.status = status;
    this.code = code;
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
    throw new ApiError(res.status, body.detail ?? `Request failed (${res.status})`, body.code ?? "http_error");
  }
  return body;
}

export function chat({ message, lat, lng, radiusKm, signal }) {
  return request("/api/v1/chat", {
    method: "POST",
    body: JSON.stringify({ message, lat, lng, radius_km: radiusKm }),
    signal,
  });
}

export function mapItems({ lat, lng, radiusKm, viewport, includeUnmatched, signal }) {
  const params = new URLSearchParams({
    lat: lat.toFixed(6),
    lng: lng.toFixed(6),
    radius_km: String(radiusKm),
    include_unmatched: String(includeUnmatched),
  });
  if (viewport) viewport.split(",").forEach(value => params.append("viewport", value));
  return request(`/api/v1/map-items?${params}`, { signal });
}

export function brandItems(slug, { signal } = {}) {
  return request(`/api/v1/brands/${encodeURIComponent(slug)}/items`, { signal });
}

export function health() {
  return request("/api/v1/health");
}
