import { Icon } from "./Brand.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import { BrandMenu } from "./MapPopup.jsx";
import { branchKey } from "./mapListings.js";
import { brandPhoto } from "./SourcePhoto.jsx";
import { useState } from "react";

function MenuDisclosure({ pin, name, goals }) {
  const [open, setOpen] = useState(false);
  return <details className="brand-menu-details" onToggle={event => setOpen(event.currentTarget.open)}><summary>View {name} menu</summary>
    {open && <BrandMenu slug={pin.matched_brand_slug} includePending goals={goals} />}
  </details>;
}

export function groupByBrand(pins) {
  const groups = new Map();
  pins.forEach(pin => {
    const key = pin.matched_brand_slug || pin.brand_name || "catalog";
    if (!groups.has(key)) groups.set(key, { key, name: pin.brand_name || "Catalog restaurants", pins: [] });
    groups.get(key).pins.push(pin);
  });
  return [...groups.values()].map(group => ({ ...group, pins: [...group.pins].sort((a, b) => (a.distance_km ?? Infinity) - (b.distance_km ?? Infinity)) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function BranchRow({ pin, onLocate, isFavorite, onToggleFavorite }) {
  return <li><button type="button" className="brand-branch-link" onClick={() => onLocate(pin)}>
    <span><strong>{pin.poi_name}</strong><small>{pin.address_hint || "Street address unavailable"}</small>
      <small>{pin.has_calorie_data ? "Reviewed items available" : "Menu awaiting review"}</small></span>
    <span className="num">{pin.distance_km.toFixed(2)} km<Icon name="arrow" size={16} /></span>
  </button><FavoriteButton pin={pin} saved={isFavorite(pin)} onToggle={onToggleFavorite} /></li>;
}

export default function BrandDirectory({ pins, onLocate, isFavorite, onToggleFavorite, goals, sortOrder = "az" }) {
  const [visible, setVisible] = useState(3);
  const groups = groupByBrand(pins).sort((a,b) => sortOrder === "nearest" ? a.pins[0].distance_km - b.pins[0].distance_km : sortOrder === "za" ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name));
  return <><div className="brand-directory">{groups.slice(0, visible).map(group => {
    const sample = group.pins[0];
    const photo = brandPhoto(group.key);
    const rows = list => <ul className="brand-branch-list">{list.map(pin => <BranchRow key={branchKey(pin)} pin={pin} onLocate={onLocate} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />)}</ul>;
    return <article className="brand-group" key={group.key}>
      <header className="brand-group-heading">{photo ? <a href={photo.source} target="_blank" rel="noreferrer" className="brand-photo-link"><img src={photo.url} alt={photo.alt} loading="lazy" /><span>Brand source photo ↗</span></a>
        : null}
        <div><h3>{group.name}</h3><p>{group.pins.length} nearby {group.pins.length === 1 ? "branch" : "branches"} · {sample.catalog_item_count ?? sample.item_count} menu items</p></div>
      </header>
      {rows(group.pins.slice(0, 1))}
      {group.pins.length > 1 && <details className="brand-more"><summary>View {group.pins.length - 1} more {group.pins.length === 2 ? "branch" : "branches"}</summary>{rows(group.pins.slice(1))}</details>}
      {sample.matched_brand_slug && <MenuDisclosure pin={sample} name={group.name} goals={goals} />}
    </article>;
  })}</div>{visible < groups.length && <button type="button" className="button-secondary more-brands" onClick={() => setVisible(value => value + 3)}>Show more restaurants ({groups.length - visible} remaining)</button>}</>;
}
