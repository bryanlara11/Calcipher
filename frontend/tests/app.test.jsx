import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import axe from "axe-core";
import App from "../src/App.jsx";
import ThemeControl, { useTheme } from "../src/components/ThemeControl.jsx";
import MealSearch, { buildMealQuery } from "../src/components/MealSearch.jsx";
import { NutritionPanel } from "../src/components/ChatPanel.jsx";
import { changeSystemTheme } from "./setup.js";
import { branchKey, mergeListings } from "../src/components/mapListings.js";
import { BranchPopup } from "../src/components/MapPopup.jsx";

vi.mock("../src/components/CalorieMap.jsx", () => ({ default: ({ onViewport }) => <div role="region" aria-label="Map renderer test stub"><button type="button" onClick={() => onViewport("14.57,14.59,120.97,120.99")}>Report test viewport</button><button type="button" onClick={() => onViewport("14.58,14.60,120.98,121.00")}>Pan test viewport</button></div> }));
const response = body => ({ ok: true, json: async () => body });
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async url => {
    if (url.includes("map-config")) return response({ provider: "osm", google_places_configured: false, osm_discovery_enabled: true });
    throw new Error(`Unexpected request: ${url}`);
  }));
});

function Themes() { const t = useTheme(); return <ThemeControl preference={t.preference} onChange={t.choose} />; }
const sample = { item_id: "fixture", food_item: "Synthetic test bowl", brand_name: "UI test fixture",
  calories: 400, protein_g: 20.25, carbs_g: null, fat_g: null, serving_size: "serving size unavailable",
  price_php: null, price_source_url: null, is_verified: false, source_post_url: "urn:synthetic-test",
  poi_name: "Synthetic test branch", distance_km: 0.5, latitude: 14.5785, longitude: 120.985, attributions: [] };

describe("appearance", () => {
  it("persists dark and light selections across remounts", async () => {
    const view = render(<Themes />);
    await userEvent.selectOptions(screen.getByLabelText("Appearance"), "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("calcipher.theme")).toBe("dark");
    view.unmount(); render(<Themes />);
    expect(screen.getByLabelText("Appearance").value).toBe("dark");
    await userEvent.selectOptions(screen.getByLabelText("Appearance"), "light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
  it("follows system changes only in System mode", async () => {
    render(<Themes />);
    act(() => changeSystemTheme(true)); expect(document.documentElement.dataset.theme).toBe("dark");
    await userEvent.selectOptions(screen.getByLabelText("Appearance"), "light");
    act(() => changeSystemTheme(true)); expect(document.documentElement.dataset.theme).toBe("light");
    await userEvent.selectOptions(screen.getByLabelText("Appearance"), "system");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
  it("still switches themes when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw Error("blocked"); });
    render(<Themes />); await userEvent.selectOptions(screen.getByLabelText("Appearance"), "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
  it("applies the saved theme before React starts", () => {
    localStorage.setItem("calcipher.theme", "dark");
    window.eval(readFileSync("public/theme-init.js", "utf8"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });
});

describe("meal search and cards", () => {
  it("preserves calorie/protein/PHP limits in a query without invented defaults", () => {
    expect(buildMealQuery({ food: "chicken", calories: "500", protein: "20", budget: "150" })).toBe("chicken under 500 kcal at least 20g protein under PHP150");
    expect(buildMealQuery({ food: "", calories: "", protein: "", budget: "" })).toBe("meal");
    expect(buildMealQuery({ food: "rice", calories: "0", protein: "0", budget: "0" })).toContain("under PHP0");
  });
  it("submits accessible dietary controls", async () => {
    const onSearch = vi.fn(); render(<MealSearch onSearch={onSearch} busy={false} />);
    expect(screen.queryByRole("spinbutton", { name: /Max calories/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Nutrition & budget" }));
    await userEvent.type(screen.getByPlaceholderText(/Chicken, noodles/), "rice");
    fireEvent.change(screen.getByLabelText(/Max calories/), { target: { value: "450" } });
    fireEvent.change(screen.getByLabelText(/Meal budget/), { target: { value: "120" } });
    await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    expect(onSearch).toHaveBeenCalledWith("rice under 450 kcal under PHP120");
  });
  it("keeps unknown values unavailable and unchecked data visibly unverified", () => {
    render(<NutritionPanel item={sample} index={1} onLocate={vi.fn()} />);
    expect(screen.getByText("Price unavailable")).toBeTruthy();
    expect(screen.getByText(/Unverified; may contain OCR errors/)).toBeTruthy();
    expect(screen.getByText("serving size unavailable")).toBeTruthy();
    expect(screen.getByText("20.25 g")).toBeTruthy();
    expect(screen.queryByText(/Checked against source/)).toBeNull();
  });
  it("shows nullable servings and preserves a reviewed community source label", () => {
    render(<NutritionPanel item={{ ...sample, serving_size: null, is_verified: true,
      source_classification: "community_reported", collected_at: "2026-11-25", secondary_source_url: "https://example.org/synthetic-entry" }} index={1} onLocate={vi.fn()} />);
    expect(screen.getByText("Serving size unavailable")).toBeTruthy();
    expect(screen.getByText("Community-reported nutrition")).toBeTruthy();
    expect(screen.queryByText("Brand-published nutrition")).toBeNull();
    expect(screen.getByRole("link", { name: "Collection source" }).href).toBe("https://example.org/synthetic-entry");
    expect(screen.getByText(/availability at this branch is not confirmed/)).toBeTruthy();
  });
  it("displays checked source prices and preserves the map locate action", async () => {
    const locate = vi.fn(); render(<NutritionPanel item={{ ...sample, price_php: 120, is_verified: true, price_source_url: "https://example.org/test-source" }} index={1} onLocate={locate} />);
    expect(screen.getByText("Source price ₱120.00")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Price source" }).href).toBe("https://example.org/test-source");
    await userEvent.click(screen.getByRole("button", { name: /Synthetic test branch/ }));
    expect(locate).toHaveBeenCalled();
  });
  it("opens chat, sends a request, and renders API-backed results", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : { results: [sample], answer: "Synthetic response", relaxation: { applied: false }, inspector: { generator_used: "template" } }));
    render(<App />);
    expect(screen.getByRole("heading", { name: "What sounds good?" })).toBeTruthy();
    expect(screen.queryByLabelText("Map renderer test stub")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.type(screen.getByLabelText("Describe what you want to eat"), "rice under PHP150");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(screen.getByText("Synthetic test bowl")).toBeTruthy());
    const request = fetch.mock.calls.find(([url]) => url.endsWith("/chat"));
    expect(JSON.parse(request[1].body).message).toBe("rice under PHP150");
    expect(request[1].headers["X-CalCipher-Map-Provider"]).toBe("osm");
    await userEvent.click(screen.getByRole("link", { name: "Search" }));
    expect(screen.queryByText("Synthetic test bowl")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(screen.getByText("Synthetic test bowl")).toBeTruthy();
  });
  it("announces API errors without inventing results", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<App />); await waitFor(() => expect(screen.getByRole("button", { name: "Find a meal" }).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Backend unreachable"));
    expect(screen.queryByText("Synthetic test bowl")).toBeNull();
  });
  it("supports direct page links and hash navigation while retaining search inputs", async () => {
    render(<App />);
    await userEvent.type(screen.getByPlaceholderText(/Chicken, noodles/), "rice");
    await userEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(screen.getByText("OpenStreetMap · no key needed")).toBeTruthy();
    expect(screen.queryByPlaceholderText(/Chicken, noodles/)).toBeNull();
    act(() => { window.location.hash = "/search"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(screen.getByPlaceholderText(/Chicken, noodles/).value).toBe("rice");
    expect(screen.getByRole("link", { name: "Search" }).getAttribute("aria-current")).toBe("page");
    act(() => { window.location.hash = "/nearby"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(screen.getByLabelText("Map renderer test stub")).toBeTruthy();
    expect(screen.queryByLabelText("Search radius")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Area & filters" }));
    expect(screen.getByLabelText("Search radius")).toBeTruthy();
  });
  it("renders a direct settings URL on refresh", () => {
    window.history.replaceState(null, "", "/#/settings");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Settings & data" })).toBeTruthy();
  });
  it("reconnects Settings after the API becomes available", async () => {
    window.history.replaceState(null, "", "/#/settings");
    fetch.mockRejectedValueOnce(new TypeError("offline"));
    render(<App />);
    await screen.findByText("API connection unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Reconnect to API" }));
    await screen.findByText("Map enabled");
    expect(screen.queryByText("API connection unavailable")).toBeNull();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Appearance" }), "dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
  it("shows one connection notice across repeated failed discovery searches", async () => {
    window.history.replaceState(null, "", "/#/chat");
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : {
      results: [], answer: "Synthetic empty result", relaxation: { applied: false },
      inspector: { generator_used: "template", discovery: [{ error: "Synthetic outage" }] },
    }));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Under 500 kcal" }).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Under 500 kcal" }));
    await screen.findByText("Synthetic empty result");
    await userEvent.click(screen.getByRole("button", { name: "Under 500 kcal" }));
    await waitFor(() => expect(screen.getAllByText("Synthetic empty result")).toHaveLength(2));
    expect(screen.getAllByText("Restaurant search couldn’t connect.")).toHaveLength(1);
    expect(screen.queryByText(/These results use any previously cached/)).toBeNull();
  });
  it("recenters discovery after panning and keeps an outage distinct from zero results", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : { branches: [], discovery: { source: "stale_cache_fallback", provider: "osm", error: "Synthetic outage" } }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await waitFor(() => expect(screen.getByText("Restaurant search couldn’t connect")).toBeTruthy());
    expect(screen.queryByText("0 restaurants nearby")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Pan test viewport" }));
    await userEvent.click(screen.getByRole("button", { name: "Search this area" }));
    await waitFor(() => {
      const requests = fetch.mock.calls.filter(([url]) => url.includes("map-items"));
      const url = new URL(requests.at(-1)[0], "http://test");
      expect(url.searchParams.get("lat")).toBe("14.590000");
      expect(url.searchParams.get("lng")).toBe("120.990000");
      expect(requests.at(-1)[1].headers["X-CalCipher-Map-Provider"]).toBe("osm");
    });
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    expect(screen.getByText("Restaurant listings are temporarily unavailable.")).toBeTruthy();
  });
  it("requests only catalog matches and excludes location-only places from Nearby", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : {
      branches: [
        { osm_type: "node", osm_id: 1, poi_name: "Synthetic catalog branch", distance_km: .2, has_calorie_data: true, item_count: 2 },
        { osm_type: "node", osm_id: 2, poi_name: "Synthetic location-only branch", distance_km: .3, has_calorie_data: false, item_count: 0 },
      ], discovery: { source: "cache", provider: "osm", error: null },
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await screen.findByText("1 restaurant with nutrition nearby");
    const url = new URL(fetch.mock.calls.find(([url]) => url.includes("map-items"))[0], "http://test");
    expect(url.searchParams.get("include_unmatched")).toBe("false");
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    expect(screen.getByText("Synthetic catalog branch")).toBeTruthy();
    expect(screen.queryByText("Synthetic location-only branch")).toBeNull();
    expect(screen.queryByText("No nutrition yet")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Area & filters" }));
    expect(screen.queryByRole("checkbox", { name: "Only places with nutrition" })).toBeNull();
  });
  it("cancels a pending meal search when its radius changes", async () => {
    let finish;
    fetch.mockImplementation(async url => url.includes("map-config") ? response({ provider: "osm" }) : new Promise(resolve => { finish = resolve; }));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Find a meal" }).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Area & filters" }));
    fireEvent.change(screen.getByLabelText("Search radius"), { target: { value: "1500" } });
    const request = fetch.mock.calls.find(([url]) => url.endsWith("/chat"));
    expect(request[1].signal.aborted).toBe(true);
    await act(async () => finish(response({ results: [sample], answer: "Synthetic response", relaxation: { applied: false }, inspector: { generator_used: "template" } })));
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(screen.getByRole("alert").textContent).toContain("Search area changed");
    expect(screen.queryByText("Synthetic test bowl")).toBeNull();
  });
  it.each(["light", "dark"].flatMap(theme => ["search", "nearby", "chat", "settings"].map(page => [theme, page])))("has no structural accessibility violations in %s %s", async (theme, page) => {
    window.history.replaceState(null, "", `/#/${page}`);
    // jsdom has no canvas renderer. SVG icons are used; color pairs are tested separately.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    localStorage.setItem("calcipher.theme", theme); render(<App />);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map(v => v.id)).toEqual([]);
  });
});

describe("map identities", () => {
  it("does not fetch a pending restaurant menu or imply it is absent from the catalog", () => {
    render(<BranchPopup pin={{ poi_name: "Synthetic branch", distance_km: .2, has_calorie_data: false }} />);
    expect(screen.getByText("No reviewed calorie data is available for this restaurant yet.")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("shows reviewed brand nutrition with provenance without promising branch availability", async () => {
    fetch.mockImplementation(async () => response({ items: [{ ...sample, is_verified: true,
      serving_size: null, source_classification: "community_reported", collected_at: "2026-11-25",
      source_post_url: "https://example.test/original" }] }));
    render(<BranchPopup pin={{ poi_name: "Synthetic branch", distance_km: .2, has_calorie_data: true,
      matched_brand_slug: "synthetic", brand_name: "Synthetic", item_count: 1, min_calories: 400 }} />);
    const source = await screen.findByRole("link", { name: "Nutrition source" });
    expect(source.getAttribute("href")).toBe("https://example.test/original");
    expect(screen.getByText(/Community-reported nutrition/)).toBeTruthy();
    expect(screen.getByText(/Serving size unavailable/)).toBeTruthy();
    expect(screen.getByText(/Item availability at this branch is unconfirmed/)).toBeTruthy();
  });
  it("keeps OSM namespaces separate and merges a recommendation into its branch", () => {
    const node = { osm_type: "node", osm_id: 10, place_id: null };
    const way = { osm_type: "way", osm_id: 10, place_id: null };
    const other = { osm_type: "node", osm_id: 11, place_id: null };
    const listings = mergeListings([node, way, other], [{ ...node, item_id: "meal" }]);
    expect(listings.size).toBe(3);
    expect(listings.get(branchKey(node)).items[0].number).toBe(1);
    expect(listings.get(branchKey(way)).items).toEqual([]);
    expect(branchKey({ place_id: "google-id" })).toBe("google:google-id");
  });
});

function luminance(rgb) { const c = rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); return c[0] * .2126 + c[1] * .7152 + c[2] * .0722; }
const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
describe("theme contrast", () => {
  const css = readFileSync("src/index.css", "utf8");
  for (const [theme, tokenBlock] of [["light", css.match(/:root \{([^}]+)\}/s)[1]], ["dark", css.match(/:root\[data-theme="dark"\] \{([^}]+)\}/s)[1]]]) {
    const rgb = name => tokenBlock.match(new RegExp(`--${name}: ([0-9 ]+);`))[1].split(" ").map(Number);
    it(`${theme} text, muted text and primary links meet AA`, () => {
      for (const name of ["ink", "muted", "primary"]) expect(ratio(rgb(name), rgb("surface"))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(rgb("muted"), rgb("canvas"))).toBeGreaterThanOrEqual(4.5);
      const hex = name => tokenBlock.match(new RegExp(`--${name}: #([A-Fa-f0-9]{6});`))[1].match(/../g).map(n => parseInt(n, 16));
      expect(ratio(hex("solid"), hex("solid-text"))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(hex("control-line"), rgb("canvas"))).toBeGreaterThanOrEqual(3);
    });
  }
});
