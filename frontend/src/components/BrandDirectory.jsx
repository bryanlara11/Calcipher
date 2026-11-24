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
    <span><strong>{pin.poi_name}</strong><small>{pin.address_hint || `${pin.latitude.toFixed(5)}, ${pin.longitude.toFixed(5)}`}</small>
      <small>{pin.catalog_item_count ?? pin.item_count} menu records{pin.pending_item_count > 0 ? ` · ${pin.pending_item_count} pending review` : " · Reviewed"}</small></span>
    <span className="num">{pin.distance_km.toFixed(2)} km<Icon name="arrow" size={16} /></span>
  </button><FavoriteButton pin={pin} saved={isFavorite(pin)} onToggle={onToggleFavorite} /></li>;
}

export default function BrandDirectory({ pins, onLocate, isFavorite, onToggleFavorite, goals }) {
  return <div className="brand-directory">{groupByBrand(pins).map(group => {
    const sample = group.pins[0];
    const photo = brandPhoto(group.key);
    const rows = list => <ul className="brand-branch-list">{list.map(pin => <BranchRow key={branchKey(pin)} pin={pin} onLocate={onLocate} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />)}</ul>;
    return <article className="brand-group" key={group.key}>
      <header className="brand-group-heading">{photo ? <a href={photo.source} target="_blank" rel="noreferrer" className="brand-photo-link"><img src={photo.url} alt={photo.alt} loading="lazy" /><span>Brand source photo ↗</span></a>
        : <span className="brand-monogram" aria-hidden="true">{group.name.split(/\s+/).slice(0,2).map(word => word[0]).join("")}</span>}
        <div><h3>{group.name}</h3><p>{group.pins.length} nearby {group.pins.length === 1 ? "branch" : "branches"} · {sample.catalog_item_count ?? sample.item_count} menu records</p></div>
      </header>
      {rows(group.pins.slice(0, 2))}
      {group.pins.length > 2 && <details className="brand-more"><summary>View {group.pins.length - 2} more branches</summary>{rows(group.pins.slice(2))}</details>}
      {sample.matched_brand_slug && <MenuDisclosure pin={sample} name={group.name} goals={goals} />}
    </article>;
  })}</div>;
}
