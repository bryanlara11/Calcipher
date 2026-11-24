import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import axe from "axe-core";
import App from "../src/App.jsx";
import ThemeControl, { useTheme } from "../src/components/ThemeControl.jsx";
import MealSearch, { buildMealQuery } from "../src/components/MealSearch.jsx";
import { NutritionPanel } from "../src/components/ChatPanel.jsx";
import { changeSystemTheme } from "./setup.js";

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
  it("displays checked source prices and preserves the map locate action", async () => {
    const locate = vi.fn(); render(<NutritionPanel item={{ ...sample, price_php: 120, is_verified: true, price_source_url: "https://example.org/test-source" }} index={1} onLocate={locate} />);
    expect(screen.getByText("Source price ₱120.00")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Price source" }).href).toBe("https://example.org/test-source");
    await userEvent.click(screen.getByRole("button", { name: /Synthetic test branch/ }));
    expect(locate).toHaveBeenCalled();
  });
  it("opens chat, sends a request, and renders API-backed results", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ results: [sample], answer: "Synthetic response", relaxation: { applied: false }, inspector: { generator_used: "template" } }) }));
    render(<App />);
    expect(screen.getByText("MAP NOT CONFIGURED")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Meal assistant" }));
    await userEvent.type(screen.getByLabelText("Describe what you want to eat"), "rice under PHP150");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Synthetic test bowl")).toBeTruthy());
    expect(JSON.parse(fetch.mock.calls[0][1].body).message).toBe("rice under PHP150");
    await userEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.queryByRole("complementary", { name: "Meal assistant" })).toBeNull();
  });
  it("announces API errors without inventing results", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<App />); await userEvent.click(screen.getByRole("button", { name: "Find a meal" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Backend unreachable"));
    expect(screen.queryByText("Synthetic test bowl")).toBeNull();
  });
  it.each(["light", "dark"])("has no structural accessibility violations in %s", async theme => {
    // jsdom has no canvas renderer. SVG icons are used; color pairs are tested separately.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    localStorage.setItem("calcipher.theme", theme); render(<App />);
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations.map(v => v.id)).toEqual([]);
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
