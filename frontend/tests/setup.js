import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

let listeners;
export function changeSystemTheme(dark) {
  window.matchMedia("(prefers-color-scheme: dark)").matches = dark;
  listeners.forEach(fn => fn({ matches: dark }));
}
beforeEach(() => {
  window.history.replaceState(null, "", "/");
  listeners = new Set();
  const media = { matches: false, addEventListener: (_type, fn) => listeners.add(fn), removeEventListener: (_type, fn) => listeners.delete(fn) };
  vi.stubGlobal("matchMedia", () => media);
  vi.stubGlobal("scrollTo", vi.fn());
  localStorage.clear();
  sessionStorage.setItem("calcipher.splashSeen", "1");
  document.documentElement.dataset.theme = "light";
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
