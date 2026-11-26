// Nearby menus and opt-in device location. All coordinates and restaurants here are synthetic.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App.jsx";
import BrandDirectory from "../src/components/BrandDirectory.jsx";
import { coarseLocation, LOCATION_TIMEOUT_MS } from "../src/deviceLocation.js";

vi.mock("../src/components/CalorieMap.jsx", () => ({ default: ({ onViewport, onMove, location }) =>
  <div role="region" aria-label="Map renderer test stub" data-lat={location.lat} data-lng={location.lng}>
    <button type="button" onClick={() => onViewport("14.57,14.59,120.97,120.99")}>Report test viewport</button>
    <button type="button" onClick={() => onMove({ lat: 14.59, lng: 120.99 })}>Move test pin</button>
  </div> }));

const json = body => ({ ok: true, json: async () => body });
const DEVICE = { latitude: 14.601234, longitude: 120.987654, accuracy: 25 };
const branch = (id, name, slug) => ({ osm_type: "node", osm_id: id, poi_name: `${name} Synthetic Branch`, brand_name: name,
  matched_brand_slug: slug, has_catalog_data: true, has_calorie_data: false, catalog_item_count: 1, pending_item_count: 1,
  item_count: 0, distance_km: id / 10, latitude: 14.58, longitude: 120.98, address_hint: "Synthetic Street" });
const menuItem = name => ({ item_id: `${name}-item`, food_item: `${name} synthetic meal`, calories: 321, protein_g: null,
  carbs_g: null, fat_g: null, serving_size: null, price_php: null, is_verified: false, source_classification: "community_reported" });

function deferred() { let resolve; let reject; const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; }

// Coordinates sent to map-items, from the query string or a JSON body.
function mapRequests() {
  return fetch.mock.calls.filter(([url]) => String(url).includes("map-items")).map(([url, options]) => {
    if (options?.body) { const body = JSON.parse(options.body); return { lat: body.lat, lng: body.lng }; }
    const params = new URL(url, "http://test").searchParams;
    return { lat: Number(params.get("lat")), lng: Number(params.get("lng")) };
  });
}

let geo;
beforeEach(() => {
  geo = { pending: [], getCurrentPosition: vi.fn((ok, fail, options) => geo.pending.push({ ok, fail, options })), watchPosition: vi.fn() };
  Object.defineProperty(navigator, "geolocation", { value: geo, configurable: true });
  vi.stubGlobal("fetch", vi.fn(async url => {
    if (url.includes("map-config")) return json({ provider: "osm", google_places_configured: false, google_fallback: false, osm_discovery_enabled: true });
    if (url.includes("map-items")) return json({ branches: [branch(1, "Alpha", "alpha"), branch(2, "Beta", "beta")], discovery: { source: "cache", provider: "osm", error: null } });
    throw new Error(`Unexpected request: ${url}`);
  }));
});
afterEach(() => { delete navigator.geolocation; delete navigator.permissions; });

async function openNearby() {
  window.history.replaceState(null, "", "/#/nearby");
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Report test viewport" }));
  await screen.findByText("2 catalog locations nearby");
}
const map = () => screen.getByRole("region", { name: "Map renderer test stub" });
const locateButton = () => screen.getByRole("button", { name: /Use my location|Finding your location/ });

describe("device location on Nearby", () => {
  it("never asks for location or sends device coordinates just by opening Nearby", async () => {
    await openNearby();
    expect(geo.getCurrentPosition).not.toHaveBeenCalled();
    expect(geo.watchPosition).not.toHaveBeenCalled();
    expect(mapRequests()).toEqual([{ lat: 14.5785, lng: 120.985 }]);
    expect(screen.getByText(/Uses your device’s location when you tap/).textContent).toContain("OpenStreetMap");
    expect(screen.getByText(/when you tap/).textContent).not.toContain("Google");
  });

  it("takes one ordinary-accuracy reading per tap and refreshes the map and list with one lookup", async () => {
    await openNearby();
    await userEvent.click(locateButton());
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(geo.pending[0].options).toMatchObject({ enableHighAccuracy: false, timeout: LOCATION_TIMEOUT_MS });
    expect(locateButton().disabled).toBe(true);
    expect(screen.getAllByText("Finding your location…").length).toBeGreaterThan(0);
    fireEvent.click(locateButton()); // a second tap while locating does nothing
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1);

    act(() => geo.pending[0].ok({ coords: DEVICE }));
    const expected = coarseLocation(DEVICE, 1000);
    expect(expected).toEqual({ lat: 14.601, lng: 120.988 });
    await waitFor(() => expect(map().dataset.lat).toBe("14.601"));
    await waitFor(() => expect(mapRequests()).toHaveLength(2));
    await new Promise(done => setTimeout(done, 500)); // past the request debounce: still one lookup
    expect(mapRequests()).toEqual([{ lat: 14.5785, lng: 120.985 }, expected]);
    const lookups = fetch.mock.calls.filter(([url]) => url.includes("map-items"));
    expect(lookups.map(([url]) => url)).toEqual(["/api/v1/map-items", "/api/v1/map-items"]);
    expect(lookups.map(([, options]) => JSON.parse(options.body).device_location)).toEqual([false, true]);
    expect(locateButton().disabled).toBe(false);
    await screen.findByText("2 catalog locations nearby");
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    expect(screen.getByText("Alpha Synthetic Branch")).toBeTruthy();

    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie;
    expect(stored).not.toMatch(/14\.60|120\.98[78]/);
  });

  it("keeps the pin and results when permission is denied", async () => {
    await openNearby();
    await userEvent.click(locateButton());
    act(() => geo.pending[0].fail({ code: 1 }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Location is blocked for this site.*Your pin hasn’t moved/);
    expect(map().dataset.lat).toBe("14.5785");
    expect(screen.getByText("2 catalog locations nearby")).toBeTruthy();
    expect(mapRequests()).toHaveLength(1);
    expect(locateButton().disabled).toBe(false);
  });

  it("explains a timeout and a reading outside coverage without moving the pin", async () => {
    await openNearby();
    await userEvent.click(locateButton());
    act(() => geo.pending[0].fail({ code: 3 }));
    expect((await screen.findByRole("alert")).textContent).toContain("took too long");
    await userEvent.click(locateButton());
    act(() => geo.pending[1].ok({ coords: { latitude: 35.6812, longitude: 139.7671 } }));
    expect((await screen.findByRole("alert")).textContent).toContain("outside the Philippines");
    expect(map().dataset.lat).toBe("14.5785");
    expect(mapRequests()).toHaveLength(1);
  });

  it("discards a reading that arrives after leaving Nearby or moving the pin by hand", async () => {
    await openNearby();
    await userEvent.click(locateButton());
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    act(() => geo.pending[0].ok({ coords: DEVICE }));
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    expect(map().dataset.lat).toBe("14.5785");

    await userEvent.click(locateButton());
    await userEvent.click(screen.getByRole("button", { name: "Move test pin" }));
    act(() => geo.pending[1].ok({ coords: DEVICE }));
    expect(map().dataset.lat).toBe("14.59");
    await new Promise(done => setTimeout(done, 450));
    expect(mapRequests().some(({ lat }) => lat === 14.601)).toBe(false);
  });

  it("forgets a device-derived pin when leaving Nearby", async () => {
    await openNearby();
    await userEvent.click(locateButton());
    act(() => geo.pending[0].ok({ coords: DEVICE }));
    await waitFor(() => expect(map().dataset.lat).toBe("14.601"));
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    expect(map().dataset.lat).toBe("14.5785");
    // The restored pin searches again on its own (a hidden List-view map reports no viewport),
    // as an ordinary pin rather than a device location.
    await waitFor(() => expect(mapRequests().at(-1)).toEqual({ lat: 14.5785, lng: 120.985 }));
    const last = fetch.mock.calls.filter(([url]) => url.includes("map-items")).at(-1);
    expect(JSON.parse(last[1].body).device_location).toBe(false);
  });
});

describe("collapsible restaurant menus", () => {
  let menus;
  beforeEach(() => {
    menus = {};
    fetch.mockImplementation(url => {
      const slug = url.match(/brands\/([^/]+)\/items/)?.[1];
      if (!slug) throw new Error(`Unexpected request: ${url}`);
      menus[slug] = menus[slug] ?? [];
      const request = deferred();
      menus[slug].push(request);
      return request.promise;
    });
  });
  const pins = [branch(1, "Alpha", "alpha"), branch(2, "Beta", "beta")];
  const renderList = () => render(<BrandDirectory pins={pins} isFavorite={() => false} onToggleFavorite={vi.fn()} onLocate={vi.fn()} />);
  const toggle = name => screen.getByRole("button", { name: new RegExp(`^(View|Hide) menu for ${name}$`) });

  it("starts collapsed, opens, closes and reopens from memory", async () => {
    renderList();
    expect(toggle("Alpha").getAttribute("aria-expanded")).toBe("false");
    expect(toggle("Alpha").textContent).toBe("View menu");
    expect(toggle("Alpha").getAttribute("aria-label")).toBe("View menu for Alpha");
    await userEvent.click(toggle("Alpha"));
    expect(toggle("Alpha").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Loading menu…")).toBeTruthy();
    await act(async () => menus.alpha[0].resolve(json({ items: [menuItem("Alpha")] })));
    await screen.findByText("Alpha synthetic meal");
    expect(document.getElementById(toggle("Alpha").getAttribute("aria-controls"))).toBeTruthy();
    // The toggle comes before the expanded menu in reading order.
    expect(toggle("Alpha").compareDocumentPosition(screen.getByText("Alpha synthetic meal")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await userEvent.click(toggle("Alpha"));
    expect(screen.queryByText("Alpha synthetic meal")).toBeNull();
    expect(toggle("Alpha").getAttribute("aria-label")).toBe("View menu for Alpha");
    await userEvent.click(toggle("Alpha"));
    expect(screen.getByText("Alpha synthetic meal")).toBeTruthy();
    expect(menus.alpha).toHaveLength(1);
  });

  it("stays closed when a response arrives after collapsing during loading", async () => {
    renderList();
    await userEvent.click(toggle("Alpha"));
    await userEvent.click(toggle("Alpha"));
    await act(async () => menus.alpha[0].resolve(json({ items: [menuItem("Alpha")] })));
    expect(toggle("Alpha").getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Alpha synthetic meal")).toBeNull();
    await userEvent.click(toggle("Alpha"));
    expect(screen.getByText("Alpha synthetic meal")).toBeTruthy(); // the late answer filled the cache
    expect(menus.alpha).toHaveLength(1);
  });

  it("collapses after an error and retries on the next open", async () => {
    renderList();
    await userEvent.click(toggle("Beta"));
    await act(async () => menus.beta[0].resolve({ ok: false, status: 503, headers: new Headers(), json: async () => ({ detail: "Synthetic outage" }) }));
    expect((await screen.findByRole("alert")).textContent).toBe("Synthetic outage");
    await userEvent.click(toggle("Beta"));
    expect(screen.queryByText("Synthetic outage")).toBeNull();
    await userEvent.click(toggle("Beta"));
    expect(menus.beta).toHaveLength(2);
    await act(async () => menus.beta[1].resolve(json({ items: [menuItem("Beta")] })));
    await screen.findByText("Beta synthetic meal");
  });

  it("keeps each restaurant's menu independent and works from the keyboard", async () => {
    renderList();
    await userEvent.click(toggle("Alpha"));
    toggle("Beta").focus();
    await userEvent.keyboard("{Enter}");
    expect(toggle("Beta").getAttribute("aria-expanded")).toBe("true");
    await act(async () => { menus.alpha[0].resolve(json({ items: [menuItem("Alpha")] })); menus.beta[0].resolve(json({ items: [menuItem("Beta")] })); });
    await screen.findByText("Beta synthetic meal");
    toggle("Alpha").focus();
    await userEvent.keyboard(" ");
    expect(toggle("Alpha").getAttribute("aria-expanded")).toBe("false");
    expect(toggle("Beta").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Beta synthetic meal")).toBeTruthy();
    const alphaCard = screen.getByRole("heading", { name: "Alpha" }).closest("article");
    expect(within(alphaCard).getByRole("button", { name: /favorites/ })).toBeTruthy();
  });
});

describe("automatic location once allowed", () => {
  const permission = state => Object.defineProperty(navigator, "permissions", { configurable: true,
    value: { query: vi.fn(async ({ name }) => ({ state: name === "geolocation" ? state : "prompt" })) } });

  it("moves the pin on opening Nearby when the site is already allowed", async () => {
    permission("granted");
    window.history.replaceState(null, "", "/#/nearby");
    render(<App />);
    await waitFor(() => expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1));
    act(() => geo.pending[0].ok({ coords: DEVICE }));
    await waitFor(() => expect(map().dataset.lat).toBe("14.601"));
    await waitFor(() => expect(mapRequests().at(-1)).toEqual({ lat: 14.601, lng: 120.988 }));
    const last = fetch.mock.calls.filter(([url]) => url.includes("map-items")).at(-1);
    expect(JSON.parse(last[1].body).device_location).toBe(true);
  });

  it("never prompts by itself when permission is undecided or blocked", async () => {
    for (const state of ["prompt", "denied"]) {
      permission(state);
      window.history.replaceState(null, "", "/#/nearby");
      const { unmount } = render(<App />);
      await userEvent.click(await screen.findByRole("button", { name: "Report test viewport" }));
      await screen.findByText("2 catalog locations nearby");
      expect(geo.getCurrentPosition).not.toHaveBeenCalled();
      unmount();
    }
  });

  it("respects the Settings switch and keeps a pin placed by hand", async () => {
    permission("granted");
    localStorage.setItem("calcipher.preferences.v1", JSON.stringify({ mapsEnabled: true, locationEnabled: false }));
    window.history.replaceState(null, "", "/#/nearby");
    const { unmount } = render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Report test viewport" }));
    await screen.findByText("2 catalog locations nearby");
    expect(geo.getCurrentPosition).not.toHaveBeenCalled();
    unmount();

    localStorage.clear();
    window.history.replaceState(null, "", "/#/nearby");
    render(<App />);
    await waitFor(() => expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: "Move test pin" })); // discards the pending reading
    act(() => geo.pending[0].ok({ coords: DEVICE }));
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    expect(map().dataset.lat).toBe("14.59");
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1);
  });
});

describe("source links", () => {
  it("labels an official brand source once and never as an Instagram post", async () => {
    const { MenuRecords } = await import("../src/components/MapPopup.jsx");
    const official = "https://corporate.kfc.com.ph/nutrition-and-allergen-information/";
    render(<MenuRecords items={[
      { ...menuItem("Synthetic official"), source_classification: "official", source_post_url: official, nutrition_source_url: official },
      { ...menuItem("Synthetic community"), item_id: "community", source_post_url: "https://www.instagram.com/p/synthetic/", nutrition_source_url: "https://example.test/calories" },
    ]} />);
    expect(screen.getAllByRole("link", { name: /^Nutrition source/ })).toHaveLength(2);
    const instagram = screen.getAllByRole("link", { name: /Instagram brand post/ });
    expect(instagram).toHaveLength(1);
    expect(instagram[0].getAttribute("href")).toBe("https://www.instagram.com/p/synthetic/");
    expect(screen.getByText("Official nutrition")).toBeTruthy();
  });
});

describe("missing macronutrients", () => {
  it("shows only the macros a record reports and drops the row when it has none", async () => {
    const { MenuRecords } = await import("../src/components/MapPopup.jsx");
    const { NutritionPanel } = await import("../src/components/ChatPanel.jsx");
    const partial = { ...menuItem("Synthetic partial"), protein_g: 12.5 };
    const none = { ...menuItem("Synthetic bare"), item_id: "bare" };
    const { container, unmount } = render(<MenuRecords items={[partial, none]} />);
    const [withProtein, bare] = container.querySelectorAll(".menu-record");
    expect(within(withProtein).getByText("Protein")).toBeTruthy();
    expect(within(withProtein).getByText("12.5 g")).toBeTruthy();
    expect(within(withProtein).queryByText("Carbs")).toBeNull();
    expect(within(withProtein).queryByText("Fat")).toBeNull();
    expect(bare.querySelector(".menu-macros")).toBeNull();
    expect(screen.queryByText(/Unavailable|—/)).toBeNull();
    unmount();

    const card = render(<NutritionPanel item={{ ...none, brand_name: "Synthetic", latitude: 14.58, longitude: 120.98, distance_km: .2 }} index={1} preview />);
    expect(card.container.querySelector(".nutrition-macros")).toBeNull();
    expect(screen.getByText("321")).toBeTruthy(); // calories still shown
  });
});

describe("List view without the map", () => {
  it("searches the pin's area when List is chosen before the map reports its view", async () => {
    window.history.replaceState(null, "", "/#/nearby");
    render(<App />);
    // The test map stub never reports a viewport on its own, like a map hidden behind List view.
    await userEvent.click(await screen.findByRole("button", { name: "List", exact: true }));
    await screen.findByText("2 catalog locations nearby");
    expect(screen.getByText("Alpha Synthetic Branch")).toBeTruthy();
    const [request] = fetch.mock.calls.filter(([url]) => url.includes("map-items"));
    const body = JSON.parse(request[1].body);
    expect(body).toMatchObject({ lat: 14.5785, lng: 120.985, radius_km: 1 });
    expect(body.viewport).toHaveLength(4); // a box around the pin's radius
  });
});
