import ThemeControl from "./ThemeControl.jsx";
import "../settings.css";

function PreferenceSwitch({ id, label, description, checked, onChange }) {
  return <div className="preference-row">
    <div className="preference-copy"><h3 id={`${id}-label`}>{label}</h3><p id={`${id}-description`}>{description}</p></div>
    <label className="preference-switch"><input type="checkbox" role="switch" aria-labelledby={`${id}-label`} aria-describedby={`${id}-description`} checked={checked} onChange={event => onChange(event.target.checked)} /><span className="switch-track" aria-hidden="true" /><span className="switch-state" aria-hidden="true">{checked ? "On" : "Off"}</span></label>
  </div>;
}

export default function SettingsPage({ headingRef, appearance, provider, configError, discoveryIssue, onReconnect, preferences = { mapsEnabled: true, locationEnabled: true }, onPreference = () => {} }) {
  return <section className="settings-page">
    <h1 ref={headingRef} tabIndex={-1}>Settings</h1><p className="page-lead">Choose your appearance and how Nearby works.</p>
    <section className="settings-group" aria-labelledby="appearance-heading">
      <h2 id="appearance-heading">Appearance</h2><p className="settings-description">Choose a background. System follows your device.</p>
      <ThemeControl preference={appearance.preference} onChange={appearance.choose} variant="buttons" />
    </section>
    <section className="settings-group" aria-labelledby="maps-heading">
      <h2 id="maps-heading">Maps & location</h2>
      <PreferenceSwitch id="maps-preference" label="Show maps" description="Turn this off to use restaurant lists in Nearby." checked={preferences.mapsEnabled} onChange={value => onPreference("mapsEnabled", value)} />
      <PreferenceSwitch id="location-preference" label="Allow location requests" description="Enable the Use my location button. Your browser still asks for permission." checked={preferences.locationEnabled} onChange={value => onPreference("locationEnabled", value)} />
      <div className="preference-row connection-row"><div className="preference-copy"><h3>Restaurant connection</h3><p className="settings-connection" data-state={configError ? "offline" : discoveryIssue ? "saved" : "online"}><span aria-hidden="true" />{provider === "google" ? "Google Maps & Places" : "OpenStreetMap"} · {configError ? "Connection unavailable" : discoveryIssue ? "Using saved restaurant locations" : "Connected"}</p></div>{configError && <button type="button" className="button-secondary" onClick={onReconnect}>Reconnect</button>}</div>
    </section>
    <section className="settings-group" aria-labelledby="saved-heading">
      <h2 id="saved-heading">Saved on this device</h2><p className="settings-description">Your favorites, meal plans and calorie targets stay in this browser.</p>
      <details className="settings-data-details"><summary>What is saved and shared?</summary><dl>
        <div><dt>On this device</dt><dd>Favorites, meal plans, targets, appearance and map preferences.</dd></div>
        <div><dt>When you search</dt><dd>The selected map area is sent to the restaurant service.</dd></div>
        <div><dt>Meal ratings</dt><dd>Ratings are shared anonymously. One rating per browser per meal can be updated.</dd></div>
      </dl></details>
    </section>
  </section>;
}
