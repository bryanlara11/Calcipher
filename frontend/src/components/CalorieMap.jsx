import { lazy, Suspense } from "react";
const OsmMap = lazy(() => import("./OsmMap.jsx"));
const GoogleMap = lazy(() => import("./GoogleMap.jsx"));

export default function CalorieMap({ provider = "osm", ...props }) {
  const Map = provider === "google" ? GoogleMap : OsmMap;
  return <Suspense fallback={<div className="map-loading" role="status">Opening the map…</div>}><Map {...props} /></Suspense>;
}
