import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import axe from "axe-core";
import App from "../src/App.jsx";
import ThemeControl, { useTheme } from "../src/components/ThemeControl.jsx";
import MealSearch, { buildMealQuery } from "../src/components/MealSearch.jsx";
import ChatPanel, { MealResults, NutritionPanel } from "../src/components/ChatPanel.jsx";
import { changeSystemTheme } from "./setup.js";
import { branchKey, mergeListings } from "../src/components/mapListings.js";
import { BranchPopup, MenuRecords } from "../src/components/MapPopup.jsx";
import GoalBadge, { matchesNutritionGoals } from "../src/components/GoalBadge.jsx";
import { FAVORITES_KEY } from "../src/favorites.js";
import { PLAN_KEY, nutritionTotals } from "../src/mealPlan.js";
import BrandDirectory, { groupByBrand } from "../src/components/BrandDirectory.jsx";
import { restaurantCategory, RESTAURANT_CATEGORIES } from "../src/restaurantCategories.js";
import { browserMapsKey } from "../src/api/client.js";

vi.mock("../src/components/CalorieMap.jsx", () => ({ default: ({ onViewport, onMove, provider }) => <div role="region" aria-label="Map renderer test stub" data-provider={provider}><button type="button" onClick={() => onViewport("14.57,14.59,120.97,120.99")}>Report test viewport</button><button type="button" onClick={() => onViewport("14.58,14.60,120.98,121.00")}>Pan test viewport</button><button type="button" onClick={() => onMove({ lat: 14.59, lng: 120.99 })}>Move test pin</button></div> }));
const response = body => ({ ok: true, json: async () => body });
beforeEach(() => {
  window.history.replaceState(null, "", "/#/search");
  vi.stubGlobal("fetch", vi.fn(async url => {
    if (url.includes("map-config")) return response({ provider: "osm", google_places_configured: false, osm_discovery_enabled: true });
    if (url.includes("catalog-summary")) return response({ records: 4, brands: 2, reviewed: 1, pending: 3 });
    throw new Error(`Unexpected request: ${url}`);
  }));
});

function Themes() { const t = useTheme(); return <ThemeControl preference={t.preference} onChange={t.choose} />; }
const sample = { item_id: "fixture", food_item: "Synthetic test bowl", brand_name: "UI test fixture",
  calories: 400, protein_g: 20.25, carbs_g: null, fat_g: null, serving_size: "serving size unavailable",
  price_php: null, price_source_url: null, is_verified: false, source_post_url: "urn:synthetic-test",
  poi_name: "Synthetic test branch", distance_km: 0.5, latitude: 14.5785, longitude: 120.985, attributions: [] };

it("sends the heavy-snack shortcut as a complete request", async () => {
  const send = vi.fn();
  render(<ChatPanel messages={[]} onSend={send} busy={false} />);
  await userEvent.click(screen.getByRole("button",{name:"Heavy snack",exact:true}));
  expect(send).toHaveBeenCalledWith("heavy snack");
});

it("keeps pizza and fast-food brands in one restaurant category", () => {
  expect(restaurantCategory({matched_brand_slug:"jollibee"})).toBe("Fast food & pizza");
  expect(restaurantCategory({matched_brand_slug:"pizza hut"})).toBe("Fast food & pizza");
  expect(RESTAURANT_CATEGORIES).not.toContain("Pizza");
});

it("keeps a labelled Settings link in the header", () => {
  render(<App />);
  expect(screen.getByRole("link",{name:"Settings",exact:true}).textContent).toContain("Settings");
});

it("sends the last successful meal context with a chat follow-up", async () => {
  window.history.replaceState(null,"","/#/chat");
  const requests=[];
  fetch.mockImplementation(async (url,options) => {
    if (url.includes("map-config")) return response({provider:"osm"});
    requests.push(JSON.parse(options.body));
    return response({answer:`Synthetic reply ${requests.length}`,results:[],catalog_matches:[],relaxation:{applied:false},
      inspector:{constraints:{semantic_query:"heavy snack",target_brand:null,max_calories:500,min_protein_g:null,max_price_php:null,max_distance_km:1}}});
  });
  render(<App />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"Heavy snack"}).disabled).toBe(false));
  await userEvent.click(screen.getByRole("button",{name:"Heavy snack"}));
  await screen.findByText("Synthetic reply 1");
  await userEvent.type(screen.getByRole("textbox",{name:"Describe what you want to eat"}),"something lighter");
  await userEvent.click(screen.getByRole("button",{name:"Send",exact:true}));
  await screen.findByText("Synthetic reply 2");
  expect(requests[1].message).toBe("something lighter");
  expect(requests[1].previous_message).toContain("heavy snack");
  expect(requests[1].previous_message).toContain("under 500 kcal");
});

it("shows menu facts without requiring or inventing branch location fields", async () => {
  const { poi_name, distance_km, latitude, longitude, ...catalogItem } = sample;
  void poi_name; void distance_km; void latitude; void longitude;
  render(<MealResults response={{answer:"Synthetic test bowl: 400 kcal.",results:[],catalog_answers:[catalogItem],relaxation:{applied:false}}} />);
  expect(screen.getByText("Synthetic test bowl: 400 kcal.")).toBeTruthy();
  await userEvent.click(screen.getByText("View menu sources and details",{exact:true}));
  expect(screen.getByRole("heading",{name:"Synthetic test bowl"})).toBeTruthy();
  expect(screen.queryByText("Synthetic test branch")).toBeNull();
});

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
    expect(onSearch).toHaveBeenCalledWith("rice under 450 kcal under PHP120", { max_calories: 450, max_price_php: 120 });
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
    expect(screen.getByRole("heading", { name: "Search menus" })).toBeTruthy();
    expect(screen.queryByLabelText("Map renderer test stub")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.type(screen.getByLabelText("Describe what you want to eat"), "rice under PHP150");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
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
  it("shows pending chat matches as catalog previews with sources and a map action", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : {
      results: [], catalog_matches: [{ ...sample, source_post_url: "https://example.test/source" }],
      answer: "Synthetic catalog matches are pending review", relaxation: { applied: false }, inspector: { generator_used: "template" },
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.click(screen.getByRole("button", { name: "Heavy snack" }));
    await screen.findByRole("region", { name: "Catalog matches pending review" });
    expect(screen.getByText("Synthetic test bowl")).toBeTruthy();
    expect(screen.getByText("Pending review; source values are not approved.")).toBeTruthy();
    expect(screen.queryByLabelText("Map pin 1")).toBeNull();
    await userEvent.click(screen.getByText("UI test fixture", { selector: "summary strong" }));
    expect(screen.getByRole("link", { name: /^Nutrition source/ }).getAttribute("href")).toBe("https://example.test/source");
    await userEvent.click(screen.getByRole("button", { name: "Synthetic test branch" }));
    expect(screen.getByRole("heading", { name: "Nearby restaurants" })).toBeTruthy();
  });
  it("uses Nearby's pin and radius in chat and marks replies from an earlier area", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" }
      : url.includes("map-items") ? { branches: [], discovery: { source: "cache", error: null } }
      : { results: [], catalog_matches: [sample], answer: "Synthetic pending reply", relaxation: { applied: false }, inspector: { generator_used: "template" } }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await userEvent.click(screen.getByRole("button", { name: "Area & filters" }));
    fireEvent.change(screen.getByLabelText("Search radius"), { target: { value: "1500" } });
    await userEvent.click(screen.getByRole("button", { name: "Move test pin" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, options]) => url.includes("map-items") && JSON.parse(options.body).lat === 14.59)).toBe(true));
    const mapRequest = fetch.mock.calls.filter(([url]) => url.includes("map-items")).at(-1);
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.type(screen.getByLabelText("Describe what you want to eat"), "KFC under 500 kcal");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Synthetic pending reply");
    const request = fetch.mock.calls.find(([url]) => url.endsWith("/chat"));
    expect(JSON.parse(request[1].body)).toMatchObject({ lat: 14.59, lng: 120.99, radius_km: 1.5 });
    expect(request[1].headers["X-CalCipher-Map-Provider"]).toBe(mapRequest[1].headers["X-CalCipher-Map-Provider"]);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    fireEvent.change(screen.getByLabelText("Search radius"), { target: { value: "2000" } });
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(screen.getByText("Earlier search area. Search again to use your current pin and radius.")).toBeTruthy();
  });
  it("supports direct page links and hash navigation while retaining search inputs", async () => {
    render(<App />);
    await userEvent.type(screen.getByPlaceholderText(/Chicken, noodles/), "rice");
    await userEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(screen.getByRole("switch", { name: "Show maps" }).checked).toBe(true);
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
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
  });
  it("reconnects Settings after the API becomes available", async () => {
    window.history.replaceState(null, "", "/#/settings");
    fetch.mockRejectedValueOnce(new TypeError("offline"));
    render(<App />);
    await screen.findByText(/OpenStreetMap · Connection unavailable/);
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await screen.findByText(/OpenStreetMap · Connected/);
    expect(screen.queryByText(/OpenStreetMap · Connection unavailable/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Dark", exact: true }));
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
      const [url, options] = requests.at(-1);
      expect(url).toBe("/api/v1/map-items"); // coordinates stay out of the URL
      expect(options.method).toBe("POST");
      expect(JSON.parse(options.body)).toMatchObject({ lat: 14.59, lng: 120.99, device_location: false });
      expect(options.headers["X-CalCipher-Map-Provider"]).toBe("osm");
    });
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    expect(screen.getByText("Restaurant listings are temporarily unavailable.")).toBeTruthy();
  });
  it("shows Google fallback places on a Google map or as a list, always with Google attribution", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm", google_fallback: true } : {
      branches: [{ osm_type: "google", osm_id: 7, poi_name: "Synthetic Google branch", distance_km: .2, has_calorie_data: false, has_catalog_data: true, catalog_item_count: 6, pending_item_count: 6, latitude: 14.5785, longitude: 120.985 }],
      discovery: { source: "live", provider: "google", error: null },
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await screen.findByText("1 catalog location nearby");
    expect(screen.getByText(/^Google Maps locations ·/)).toBeTruthy();
    const map = screen.queryByRole("region", { name: "Map renderer test stub" });
    if (browserMapsKey) {
      expect(map.dataset.provider).toBe("google");
      await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    } else {
      expect(map).toBeNull(); // no Google map available: Google data must not sit on the OSM map
      expect(screen.getByRole("button", { name: "Map", exact: true }).disabled).toBe(true);
    }
    expect(screen.getByText("Synthetic Google branch")).toBeTruthy();
    expect(screen.getByText("Restaurant locations from Google Maps.")).toBeTruthy();
    expect(fetch.mock.calls.find(([url]) => url.includes("map-items"))[1].headers["X-CalCipher-Map-Provider"]).toBe("osm");
  });
  it("requests only catalog matches and excludes location-only places from Nearby", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : {
      branches: [
        { osm_type: "node", osm_id: 1, poi_name: "Synthetic catalog branch", distance_km: .2, has_calorie_data: true, has_catalog_data: true, item_count: 2, catalog_item_count: 2, latitude: 14.5785, longitude: 120.985, address_hint: "Synthetic Taft address" },
        { osm_type: "node", osm_id: 2, poi_name: "Synthetic location-only branch", distance_km: .3, has_calorie_data: false, item_count: 0 },
        { osm_type: "node", osm_id: 3, poi_name: "Synthetic pending branch", distance_km: .4, has_calorie_data: false, has_catalog_data: true, catalog_item_count: 6, pending_item_count: 6, latitude: 14.5785, longitude: 120.985 },
      ], discovery: { source: "cache", provider: "osm", error: null },
    }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await screen.findByText("2 catalog locations nearby");
    const body = JSON.parse(fetch.mock.calls.find(([url]) => url.includes("map-items"))[1].body);
    expect(body.include_unmatched).toBe(false);
    expect(body.include_pending).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    expect(screen.getByText("Synthetic catalog branch")).toBeTruthy();
    expect(screen.getByText("Synthetic pending branch")).toBeTruthy();
    expect(screen.getByText("Synthetic Taft address")).toBeTruthy();
    expect(screen.getByText("Includes reviewed nutrition")).toBeTruthy();
    expect(screen.queryByText("Synthetic location-only branch")).toBeNull();
    expect(screen.queryByText("No nutrition yet")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Area & filters" }));
    expect(screen.queryByRole("checkbox", { name: "Only places with nutrition" })).toBeNull();
  });
  it("cancels a pending Chat request when its radius changes", async () => {
    let finish;
    fetch.mockImplementation(async url => url.includes("map-config") ? response({ provider: "osm" }) : new Promise(resolve => { finish = resolve; }));
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    await userEvent.click(screen.getByRole("button", { name: "Heavy snack" }));
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
  it.each(["light", "dark"].flatMap(theme => ["home", "search", "nearby", "chat", "plan", "favorites", "settings", "data"].map(page => [theme, page])))("has no structural accessibility violations in %s %s", async (theme, page) => {
    window.history.replaceState(null, "", `/#/${page}`);
    // jsdom has no canvas renderer. SVG icons are used; color pairs are tested separately.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    localStorage.setItem("calcipher.theme", theme); render(<App />);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map(v => v.id)).toEqual([]);
  });
});

describe("map identities", () => {
  it("browses pending menu records with source links without claiming review", async () => {
    fetch.mockImplementation(async () => response({ items: [{ ...sample, is_verified: false,
      source_post_url: "https://example.test/pending", serving_size: null, source_classification: "community_reported" }] }));
    render(<BranchPopup pin={{ poi_name: "Synthetic pending", distance_km: .2, has_catalog_data: true,
      has_calorie_data: false, matched_brand_slug: "synthetic", brand_name: "Synthetic", catalog_item_count: 1, pending_item_count: 1, item_count: 0 }} />);
    await userEvent.click(screen.getByRole("button", { name: "View menu for Synthetic pending" }));
    await screen.findByRole("link", { name: /^Nutrition source/ });
    expect(fetch.mock.calls[0][0]).toContain("include_pending=true");
    expect(screen.getByText("1 pending source review")).toBeTruthy();
    expect(screen.getByText("Serving size unavailable")).toBeTruthy();
    expect(screen.getByText("Pending review")).toBeTruthy();
    expect(screen.queryByText(/Checked against source/)).toBeNull();
  });
  it("does not fetch a location-only menu or imply it is absent from the catalog", () => {
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
    await userEvent.click(screen.getByRole("button", { name: "View menu for Synthetic branch" }));
    const source = await screen.findByRole("link", { name: /^Nutrition source/ });
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

describe("home, goals and saved restaurants", () => {
  const pin = { osm_type: "node", osm_id: 91, poi_name: "Synthetic saved branch", distance_km: .2,
    has_catalog_data: true, has_calorie_data: false, catalog_item_count: 6, pending_item_count: 6,
    brand_name: "Synthetic", matched_brand_slug: "synthetic", latitude: 14.58, longitude: 120.985, address_hint: "Synthetic Street" };
  const mockPlaces = () => fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" }
    : { branches: [pin, { ...pin, osm_type: "way", poi_name: "Synthetic second branch" }], discovery: { source: "cache", error: null } }));
  it("opens Home by default and explains how to start a meal search", async () => {
    window.history.replaceState(null, "", "/"); render(<App />);
    expect(screen.getByRole("heading", { name: "Calorie-counted meals near you" })).toBeTruthy();
    await userEvent.click(screen.getByText("How it works", { exact: true }));
    expect(screen.getByRole("heading", { name: "Pick a place" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "About menu data" })).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "Find a meal" }));
    expect(screen.getByRole("heading", { name: "Search menus" })).toBeTruthy();
  });
  it("keeps catalog searches, filters and results separate from Chat history and follow-ups", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" }
      : url.endsWith("/catalog-search") ? { items: [sample], filters: { max_calories: 500 } }
      : { results: [], answer: "Chat reply", relaxation: { applied: false } }));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Find a meal" }).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Nutrition & budget" }));
    await userEvent.type(screen.getByLabelText(/Max calories/), "500");
    await userEvent.type(screen.getByLabelText("Food or dish"), "chicken");
    await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    await screen.findByRole("region", { name: "Meal search results" });
    await userEvent.click(await screen.findByText(sample.brand_name, { selector: "summary strong" }));
    await screen.findByText("Synthetic test bowl");
    expect(window.location.hash).toBe("#/search");
    const submitted = fetch.mock.calls.find(([url]) => url.endsWith("/catalog-search"));
    expect(JSON.parse(submitted[1].body)).toEqual({ message: "chicken under 500 kcal", filters: { max_calories: 500 } });
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/chat"))).toBe(false);
    expect(screen.queryByAltText("CalCipher community pick seal")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Chat" }));
    expect(screen.getByRole("heading", { name: "Search for a meal" })).toBeTruthy(); // Chat is still empty
    await userEvent.click(screen.getByRole("button", { name: "Heavy snack" }));
    await screen.findByText("Chat reply");
    const chatBody = JSON.parse(fetch.mock.calls.find(([url]) => url.endsWith("/chat"))[1].body);
    expect(chatBody).not.toHaveProperty("previous_message");
    expect(chatBody).not.toHaveProperty("filters");
    await userEvent.click(screen.getByRole("link", { name: "Search" }));
    expect(screen.getByText("1 menu item across 1 restaurant")).toBeTruthy();
    expect(screen.getByLabelText("Food or dish").value).toBe("chicken");
    expect(screen.queryByText("Chat reply")).toBeNull();
  });
  it("saves separate OSM identities, restores them after remount and removes a favorite", async () => {
    mockPlaces(); const view = render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await screen.findByText("2 catalog locations nearby");
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Add Synthetic saved branch to favorites" }));
    expect(screen.getByText("Added Synthetic saved branch to favorites.").closest("[aria-live=polite]")).toBeTruthy();
    await userEvent.click(screen.getByText("View 1 more branch"));
    await userEvent.click(screen.getByRole("button", { name: "Add Synthetic second branch to favorites" }));
    expect(JSON.parse(localStorage.getItem(FAVORITES_KEY))).toHaveLength(2);
    await userEvent.click(screen.getByRole("link", { name: "Favorites" }));
    expect(screen.getByRole("heading", { name: "Synthetic saved branch" })).toBeTruthy();
    view.unmount(); render(<App />);
    expect(screen.getByRole("heading", { name: "Synthetic second branch" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Remove Synthetic saved branch from favorites" }));
    expect(screen.getByText("Removed Synthetic saved branch from favorites.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Synthetic saved branch" })).toBeNull();
    expect(JSON.parse(localStorage.getItem(FAVORITES_KEY))).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Explore this area" }));
    expect(screen.getByRole("heading", { name: "Nearby restaurants" })).toBeTruthy();
  });
  it("keeps favorites usable in memory when browser storage is blocked", async () => {
    mockPlaces(); vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw Error("blocked"); });
    render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Nearby" }));
    await userEvent.click(screen.getByRole("button", { name: "Report test viewport" }));
    await screen.findByText("2 catalog locations nearby");
    await userEvent.click(screen.getByRole("button", { name: "List", exact: true }));
    await userEvent.click(screen.getByRole("button", { name: "Add Synthetic saved branch to favorites" }));
    await userEvent.click(screen.getByRole("link", { name: "Favorites" }));
    expect(screen.getByRole("heading", { name: "Synthetic saved branch" })).toBeTruthy();
    expect(screen.getByText(/This browser cannot save favorites between visits/)).toBeTruthy();
  });
  it("requires selected nutrition goals and known values for a goal match", () => {
    expect(matchesNutritionGoals(sample, {})).toBe(false);
    expect(matchesNutritionGoals(sample, { max_calories: "", min_protein_g: null })).toBe(false);
    expect(matchesNutritionGoals(sample, { max_calories: 500, min_protein_g: 20 })).toBe(true);
    expect(matchesNutritionGoals({ ...sample, protein_g: null }, { min_protein_g: 20 })).toBe(false);
    expect(matchesNutritionGoals(sample, { max_calories: 300 })).toBe(false);
  });
  it("awards the badge only from enough real community rating data", () => {
    const view = render(<GoalBadge item={sample} />);
    expect(screen.queryByAltText("CalCipher community pick seal")).toBeNull();
    view.rerender(<GoalBadge item={{ ...sample, rating_count: 3, rating_average: 4.5, community_pick: true }} />);
    expect(screen.getByAltText("CalCipher community pick seal")).toBeTruthy();
    expect(screen.getByText("Community pick")).toBeTruthy();
    expect(screen.getByText(/Nutrition pending review/)).toBeTruthy();
    view.rerender(<GoalBadge item={{ ...sample, rating_count: 1, rating_average: 5, community_pick: false }} />);
    expect(screen.queryByAltText("CalCipher community pick seal")).toBeNull();
  });
  it("groups nearby branches by brand and keeps the closest branch first", () => {
    const groups = groupByBrand([
      { ...pin, distance_km: 2 }, { ...pin, osm_id: 92, distance_km: .1 },
      { ...pin, brand_name: "Another brand", matched_brand_slug: "another", osm_id: 93 },
    ]);
    expect(groups.map(group => group.name)).toEqual(["Another brand", "Synthetic"]);
    expect(groups[1].pins.map(item => item.distance_km)).toEqual([.1, 2]);
  });
  it("opens menu data only through Home and keeps it out of Settings and the main navigation", async () => {
    window.history.replaceState(null, "", "/#/home"); render(<App />);
    await userEvent.click(screen.getByRole("link", { name: "Read about the data" }));
    expect(screen.getByRole("heading", { name: "About menu data", level: 1 })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "About menu data" })).toBeNull();
    await screen.findByText("Menu records");
    await userEvent.click(screen.getByRole("link", { name: "Settings", exact: true }));
    expect(screen.getByRole("heading", { name: "Settings", level: 1 })).toBeTruthy();
    expect(screen.queryByText("Menu records")).toBeNull();
  });
  it("saves a pending meal to a plan, restores it and removes it without inventing missing macros", async () => {
    fetch.mockImplementation(async url => response(url.includes("map-config") ? { provider: "osm" } : {
      items: [{ ...sample, calories_min: 446, calories: 576 }], filters: {},
    }));
    const view = render(<App />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Find a meal" }).disabled).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    await userEvent.click(await screen.findByText("UI test fixture", { selector: "summary strong" }));
    await screen.findByText("Synthetic test bowl");
    await userEvent.click(screen.getByText("Add to meal plan"));
    await userEvent.click(screen.getByRole("button", { name: "Save meal" }));
    await userEvent.click(screen.getByRole("link", { name: "Meal plan", exact: true }));
    expect(screen.getByRole("heading", { name: "Synthetic test bowl" })).toBeTruthy();
    expect(screen.getByText(/Pending review ·/)).toBeTruthy();
    // The only planned meal has protein but no carbs or fat: those totals are hidden, not blank.
    const totals = document.querySelector(".plan-totals");
    expect(within(totals).getByText("Protein")).toBeTruthy();
    expect(within(totals).getByText("20.25 g")).toBeTruthy();
    expect(within(totals).queryByText("Carbs")).toBeNull();
    expect(within(totals).queryByText(/Unavailable|missing/)).toBeNull();
    expect(JSON.parse(localStorage.getItem(PLAN_KEY))).toHaveLength(1);
    view.unmount(); render(<App />);
    expect(screen.getByRole("heading", { name: "Synthetic test bowl" })).toBeTruthy();
    expect(screen.getAllByText("446–576 kcal").length).toBeGreaterThanOrEqual(2);
    await userEvent.click(screen.getByRole("button", { name: "Remove Synthetic test bowl from meal plan" }));
    expect(screen.queryByRole("heading", { name: "Synthetic test bowl" })).toBeNull();
    expect(JSON.parse(localStorage.getItem(PLAN_KEY))).toEqual([]);
  });
  it("reports known totals and missing values separately", () => {
    const entries = [{ item: { protein_g: 20 } }, { item: { protein_g: null } }];
    expect(nutritionTotals(entries, "protein_g")).toEqual({ known: 20, missing: 1 });
    expect(nutritionTotals(entries, "fat_g")).toEqual({ known: 0, missing: 2 });
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

it("separates external nutrition evidence from the Instagram brand origin", () => {
  render(<NutritionPanel item={{ ...sample, source_post_url: "https://www.instagram.com/p/synthetic/",
    nutrition_source_url: "https://example.org/official-menu.pdf", secondary_source_url: "https://example.org/official-menu.pdf" }}
    index={1} onLocate={vi.fn()} />);
  expect(screen.getByRole("link", { name: "Nutrition source" }).getAttribute("href")).toBe("https://example.org/official-menu.pdf");
  expect(screen.getByRole("link", { name: "Instagram brand post" }).getAttribute("href")).toBe("https://www.instagram.com/p/synthetic/");
  expect(screen.queryByRole("link", { name: "Collection source" })).toBeNull();
});

it("stores the daily target and provides a seven-day calendar with month marked coming soon", async () => {
  window.history.replaceState(null,"","/#/plan"); const view = render(<App />);
  const day = screen.getByLabelText("Meal plan date").value;
  await userEvent.type(screen.getByLabelText("Daily calorie target"),"1900");
  expect(screen.getByRole("progressbar",{name:"Daily calorie progress"}).getAttribute("max")).toBe("1900");
  expect(JSON.parse(localStorage.getItem("calcipher.planTargets.v1"))[day]).toBe(1900);
  await userEvent.click(screen.getByRole("button",{name:"Week",exact:true}));
  expect(screen.getAllByRole("button",{name:/: 0 meals$/})).toHaveLength(7);
  expect(screen.getByRole("button",{name:/Month/}).disabled).toBe(true);
  view.unmount(); render(<App />);
  expect(screen.getByLabelText("Daily calorie target").value).toBe("1900");
});

it("lets users disable map display and location requests while keeping restaurant lists", async () => {
  window.history.replaceState(null,"","/#/settings"); render(<App />);
  await userEvent.click(screen.getByRole("switch",{name:"Show maps"}));
  await userEvent.click(screen.getByRole("switch",{name:"Allow location requests"}));
  await userEvent.click(screen.getByRole("link",{name:"Nearby",exact:true}));
  expect(screen.queryByRole("region",{name:"Map renderer test stub"})).toBeNull();
  expect(screen.getByRole("button",{name:"Map",exact:true}).disabled).toBe(true);
  await userEvent.click(screen.getByRole("button",{name:"Area & filters"}));
  expect(screen.getByRole("button",{name:"Use my location"}).disabled).toBe(true);
});

it("shows three alphabetically sorted restaurant brands first and expands the rest", async () => {
  const pins = ["Zeta", "Alpha", "Delta", "Beta"].map((name,index)=>({osm_type:"node",osm_id:index+100,brand_name:name,poi_name:name,matched_brand_slug:name.toLowerCase(),latitude:14.58,longitude:120.98,distance_km:1,catalog_item_count:1,pending_item_count:1}));
  render(<BrandDirectory pins={pins} isFavorite={()=>false} onToggleFavorite={vi.fn()} onLocate={vi.fn()} />);
  expect(screen.getAllByRole("heading",{level:3}).map(h=>h.textContent)).toEqual(["Alpha","Beta","Delta"]);
  expect(screen.queryByRole("heading",{name:"Zeta"})).toBeNull();
  await userEvent.click(screen.getByRole("button",{name:/Show more restaurants/}));
  expect(screen.getByRole("heading",{name:"Zeta"})).toBeTruthy();
});

it("submits a genuine rating action and does not create a badge from one vote", async () => {
  fetch.mockImplementation(async url => response(url.includes("map-config") ? {provider:"osm"}
    : url.includes("meal-ratings") ? {rating_count:1,rating_average:5,community_pick:false}
    : {items:[sample],filters:{}}));
  render(<App />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"Find a meal"}).disabled).toBe(false));
  await userEvent.click(screen.getByRole("button",{name:"Find a meal"}));
  await userEvent.click(await screen.findByText("UI test fixture",{selector:"summary strong"}));
  await userEvent.click(screen.getByText("Rate as a healthy choice"));
  await userEvent.click(screen.getByRole("button",{name:"5 stars"}));
  await screen.findByText("5.0/5 · 1 community rating");
  const request = fetch.mock.calls.find(([url])=>url.includes("meal-ratings"));
  expect(JSON.parse(request[1].body).score).toBe(5);
  expect(JSON.parse(request[1].body).voter_id).toMatch(/^[0-9a-f-]{36}$/i);
  expect(screen.queryByAltText("CalCipher community pick seal")).toBeNull();
});

describe("catalog-wide chat answers", () => {
  const base = { results: [], catalog_matches: [], relaxation: { applied: false }, inspector: { generator_used: "template", constraints: {} } };
  const kfc = { ...sample, item_id: "a", food_item: "Zinger", brand_name: "KFC", protein_g: 32 };
  const pecho = { ...sample, item_id: "b", food_item: "Chicken Inasal Pecho", brand_name: "Mang Inasal", protein_g: 45 };
  it("shows a ranking in the server's order with each item's restaurant", () => {
    render(<MealResults response={{ ...base, answer: "Highest protein:", menu_matches: [pecho, kfc], menu_match_count: 2,
      ranking: { field: "protein_g", direction: "desc" } }} onLocate={() => {}} />);
    const list = screen.getByRole("list", { name: "Highest protein first" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map(row => row.querySelector("h3").textContent)).toEqual(["1.Chicken Inasal PechoMang Inasal", "2.ZingerKFC"]);
    expect(list.tagName).toBe("OL");
  });
  it("groups plain food matches by restaurant without repeating the answer's counts", async () => {
    render(<MealResults response={{ ...base, answer: "2 menu items matching “chicken” from 2 restaurants.", menu_matches: [kfc, pecho],
      menu_match_count: 2, ranking: null }} onLocate={() => {}} />);
    expect(screen.getByText("KFC", { selector: "summary strong" })).toBeTruthy();
    expect(screen.getByText("Mang Inasal", { selector: "summary strong" })).toBeTruthy();
    expect(screen.queryByText(/across 2 restaurants/)).toBeNull();
    await userEvent.click(screen.getByText("KFC", { selector: "summary strong" }));
    expect(screen.getByText("Zinger")).toBeTruthy();
  });
});

it("shows a menu item's own note under its serving and leaves it out when there is none", () => {
  render(<MenuRecords items={[{ ...sample, item_id: "n1", food_item: "Fried Chicken Meal", serving_size: "1 serving", menu_note: "With Rice and Drink" },
    { ...sample, item_id: "n2", food_item: "Plain item", serving_size: "1 serving", menu_note: null }]} />);
  const [withNote, withoutNote] = screen.getAllByRole("listitem");
  expect(within(withNote).getByText("With Rice and Drink").className).toBe("menu-note");
  expect(withoutNote.querySelector(".menu-note")).toBeNull();
});
