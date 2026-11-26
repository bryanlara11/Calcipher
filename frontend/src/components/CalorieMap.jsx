import { Component, lazy, Suspense } from "react";
const OsmMap = lazy(() => import("./OsmMap.jsx"));
const GoogleMap = lazy(() => import("./GoogleMap.jsx"));

export class MapBoundary extends Component {
  state = { failed: false, attempt: 0 };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <div className="map-empty" role="status"><h3>The map couldn’t open</h3><p>You can still browse the restaurant list.</p><button type="button" className="button-secondary" onClick={() => this.setState(state => ({ failed: false, attempt: state.attempt + 1 }))}>Try the map again</button></div>;
    return <div key={this.state.attempt} className="map-renderer">{this.props.children}</div>;
  }
}
export default function CalorieMap({ provider = "osm", ...props }) {
  const Map = provider === "google" ? GoogleMap : OsmMap;
  return <MapBoundary key={provider}><Suspense fallback={<div className="map-loading" role="status">Opening the map…</div>}><Map {...props} /></Suspense></MapBoundary>;
}
