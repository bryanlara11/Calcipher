import { useEffect, useState } from "react";
import { Icon } from "./Brand.jsx";

export const THEME_KEY = "calcipher.theme";
export function readThemePreference() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return ["light", "dark", "system"].includes(value) ? value : "system";
  } catch { return "system"; }
}

export function useTheme() {
  const [preference, setPreference] = useState(readThemePreference);
  const [systemDark, setSystemDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  const theme = preference === "system" ? (systemDark ? "dark" : "light") : preference;
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const changed = event => setSystemDark(event.matches);
    media.addEventListener("change", changed);
    const stored = event => { if (event.key === THEME_KEY || event.key === null) setPreference(readThemePreference()); };
    window.addEventListener("storage", stored);
    return () => { media.removeEventListener("change", changed); window.removeEventListener("storage", stored); };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  function choose(value) {
    if (!["light", "dark", "system"].includes(value)) return;
    setPreference(value);
    try { localStorage.setItem(THEME_KEY, value); } catch { /* still works for this visit */ }
  }
  return { preference, theme, choose };
}

export default function ThemeControl({ preference, onChange }) {
  return <label className="theme-control"><Icon name="sun" size={17} />
    <span className="sr-only">Appearance</span>
    <select aria-label="Appearance" value={preference} onChange={event => onChange(event.target.value)}>
      <option value="light">Light</option><option value="dark">Dark</option><option value="system">System</option>
    </select>
  </label>;
}
