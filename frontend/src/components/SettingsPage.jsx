import ThemeControl from "./ThemeControl.jsx";

export default function SettingsPage({ headingRef, appearance, provider, configError, discoveryIssue, onReconnect, preferences = { mapsEnabled: true, locationEnabled: true }, onPreference = () => {} }) {
  return <section className="settings-page">
    <h1 ref={headingRef} tabIndex={-1}>Settings</h1><p className="page-lead">Make CalCipher work the way you prefer.</p>
    <div className="settings-list">
      <section className="setting-row"><div><h2>Appearance</h2><p>Use a light or dark background, or follow your device.</p></div><ThemeControl preference={appearance.preference} onChange={appearance.choose} /></section>
      <section className="setting-row"><div><h2>Interactive maps</h2><p>Show the map in Nearby. When off, you can use restaurant lists and menu search.</p></div><label className="setting-toggle"><input type="checkbox" checked={preferences.mapsEnabled} onChange={event => onPreference("mapsEnabled", event.target.checked)} /><span>Show maps</span></label></section>
      <section className="setting-row"><div><h2>Device location</h2><p>Allow the “Use my location” button. Your browser will still ask before sharing your location.</p></div><label className="setting-toggle"><input type="checkbox" checked={preferences.locationEnabled} onChange={event => onPreference("locationEnabled", event.target.checked)} /><span>Allow location requests</span></label></section>
      <section className="setting-row"><div><h2>Restaurant connection</h2><p>{provider === "google" ? "Google Maps & Places" : "OpenStreetMap"} · {configError ? "Connection unavailable" : discoveryIssue ? "Using saved restaurant locations" : "Connected"}</p><p>Menus and restaurant locations may be updated at different times.</p></div>{configError && <button type="button" className="button-secondary" onClick={onReconnect}>Reconnect</button>}</section>
      <section className="setting-row"><div><h2>Saved on this device</h2><p>Favorites, meal plans, targets and appearance are saved in this browser. The selected area is sent to the restaurant service when you search. Meal ratings are shared anonymously; one rating per browser per meal can be updated.</p></div></section>
    </div>
  </section>;
}
