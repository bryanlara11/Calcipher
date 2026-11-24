import { useState } from "react";
import { Icon } from "./Brand.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import { BrandMenu } from "./MapPopup.jsx";
import { branchKey } from "./mapListings.js";

function SavedRestaurant({ pin, provider, onExplore, onToggle, goals }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const sameProvider = Boolean(pin.place_id) === (provider === "google");
  return <article className="favorite-card">
    <div className="favorite-heading"><div><h2>{pin.poi_name}</h2><p>{pin.brand_name || "Saved restaurant"}</p></div><FavoriteButton pin={pin} saved onToggle={onToggle} /></div>
    <p className="favorite-address"><Icon name="pin" size={17} />{pin.address_hint || `${pin.latitude.toFixed(5)}, ${pin.longitude.toFixed(5)}`}</p>
    <div className="favorite-actions">{sameProvider ? <button className="button-secondary" type="button" onClick={() => onExplore(pin)}>Explore this area<Icon name="arrow" size={16} /></button>
      : <a className="button-secondary" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/search/?${new URLSearchParams({ api: "1", query: pin.poi_name, ...(pin.place_id ? { query_place_id: pin.place_id } : {}) })}`}>Open map ↗</a>}
      {pin.matched_brand_slug && <button className="text-button" type="button" aria-expanded={menuOpen} onClick={() => setMenuOpen(v => !v)}>{menuOpen ? "Hide menu" : "View menu"}</button>}
    </div>
    {menuOpen && <BrandMenu slug={pin.matched_brand_slug} includePending goals={goals} />}
  </article>;
}

export default function FavoritesPage({ headingRef, favorites, provider, onExplore, onToggle, storageUnavailable, goals }) {
  return <section className="favorites-page"><h1 ref={headingRef} tabIndex={-1}>Your favorites</h1><p className="page-lead">Restaurants saved on this device, ready for your next meal.</p>
    {storageUnavailable && <p role="status" className="page-alert">This browser cannot save favorites between visits. You can still use them during this visit.</p>}
    {favorites.length ? <div className="favorites-grid">{favorites.map(pin => <SavedRestaurant key={branchKey(pin)} pin={pin} provider={provider} onExplore={onExplore} onToggle={onToggle} goals={goals} />)}</div>
      : <div className="favorites-empty"><Icon name="heart" size={36} /><h2>Keep your go-to places here.</h2><p>Tap the heart on a restaurant in Nearby or a meal result to save it.</p><a className="button-primary" href="#/nearby">Find restaurants<Icon name="arrow" size={18} /></a></div>}
  </section>;
}
