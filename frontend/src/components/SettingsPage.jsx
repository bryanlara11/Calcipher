import { Icon } from "./Brand.jsx";
import ThemeControl from "./ThemeControl.jsx";

export default function SettingsPage({ headingRef, appearance, provider, config, configError, discoveryIssue, onReconnect, browserKeyConfigured }) {
  const offline = Boolean(configError || discoveryIssue);
  return <section className="settings-page">
    <h1 ref={headingRef} tabIndex={-1}>Settings</h1>
    <p className="page-lead">Choose your appearance and manage the map connection.</p>
    <div className="settings-grid">
      <section className="settings-card appearance-card" aria-labelledby="appearance-heading">
        <div><h2 id="appearance-heading"><Icon name="sun" />Appearance</h2><p>Light, dark, or your device setting.</p></div>
        <ThemeControl preference={appearance.preference} onChange={appearance.choose} />
      </section>
      <section className="settings-card" aria-labelledby="connection-heading">
        <h2 id="connection-heading"><Icon name="pin" />Restaurant locations</h2>
        <p className="provider-label">{provider === "osm" ? "OpenStreetMap · no key needed" : "Google Maps & Places"}</p>
        <p className={`connection-state ${offline ? "is-offline" : ""}`} role="status"><span className="connection-dot" />{configError ? "API connection unavailable" : discoveryIssue ? "Live search couldn’t connect" : "Map enabled"}</p>
        <p>{offline ? "The map is still available. Try another search from Nearby." : "Move your pin or change the radius to find restaurants."}</p>
        <a className="settings-link" href="#/nearby">Open nearby map<Icon name="arrow" size={17} /></a>
        {configError && <button type="button" className="text-button reconnect-button" onClick={onReconnect}>Reconnect to API</button>}
        {configError && <details className="settings-disclosure"><summary>Connection details</summary><p>{configError}</p></details>}
      </section>
      <section className="settings-card" aria-labelledby="privacy-heading">
        <h2 id="privacy-heading"><Icon name="info" />Location & privacy</h2>
        <p>Your location is requested only when you choose “Use my location.” You can also move the map pin yourself.</p>
        <p>Favorites stay in this browser. The selected search area is sent to the restaurant service to find nearby places.</p>
      </section>
      <details className="settings-card settings-advanced"><summary>Google Maps setup<span>Optional</span></summary>
        <p>The no-key map works without Google. To switch, configure both keys and restart the API and Vite.</p>
        <ul><li>Browser: <code>VITE_GOOGLE_MAPS_API_KEY</code> in <code>frontend/.env</code>, restricted to Maps JavaScript API and your website origins.</li><li>Server: <code>GOOGLE_PLACES_API_KEY</code> in <code>backend/.env</code>, restricted to Places API (New).</li></ul>
        <p>Enable both APIs and billing in your Google Cloud project. The server key stays on the server.</p>
        <dl className="key-status"><div><dt>Browser key</dt><dd>{browserKeyConfigured ? "Present" : "Not configured"}</dd></div><div><dt>Places key</dt><dd>{config?.google_places_configured ? "Present" : "Not configured"}</dd></div></dl>
      </details>
    </div>
  </section>;
}
