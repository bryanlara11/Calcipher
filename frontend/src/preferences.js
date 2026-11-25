import { useEffect, useState } from "react";
export const PREFERENCES_KEY = "calcipher.preferences.v1";
export function usePreferences() {
  const [preferences, setPreferences] = useState(() => {
    try { const saved = JSON.parse(localStorage.getItem(PREFERENCES_KEY) || "{}");
      return { mapsEnabled: saved.mapsEnabled !== false, locationEnabled: saved.locationEnabled !== false }; }
    catch { return { mapsEnabled: true, locationEnabled: true }; }
  });
  useEffect(() => { try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences)); } catch { /* use this visit */ } }, [preferences]);
  return [preferences, (key, value) => setPreferences(current => ({ ...current, [key]: value }))];
}
