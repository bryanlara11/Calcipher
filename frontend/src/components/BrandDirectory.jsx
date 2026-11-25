import { Icon } from "./Brand.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import { BrandMenu, MenuToggle } from "./MapPopup.jsx";
import { branchKey } from "./mapListings.js";
import { brandPhoto } from "./SourcePhoto.jsx";
import { useCallback, useRef, useState } from "react";
import "../nearby-list.css";

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

const formatDistance = km => Number.isFinite(km) ? `${km.toFixed(2)} km` : "";

function BranchRow({ pin, onLocate, isFavorite, onToggleFavorite }) {
  return <li className="branch-row">
    <button type="button" className="branch-row-main" onClick={() => onLocate(pin)}>
      <span className="branch-row-text"><strong>{pin.poi_name}</strong><small>{pin.address_hint || "Street address unavailable"}</small></span>
      <span className="branch-row-distance num">{formatDistance(pin.distance_km)}<Icon name="arrow" size={15} /></span>
    </button>
    <FavoriteButton pin={pin} saved={isFavorite(pin)} onToggle={onToggleFavorite} />
  </li>;
}

function BrandMenuSection({ group, menuKey, open, onToggle, goals }) {
  const sample = group.pins[0];
  const toggle = useRef(null);
  const id = `menu-${menuKey.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  function handleToggle() {
    onToggle(menuKey);
    // Closing a long menu from the sticky button: keep the button (and this restaurant) on screen.
    if (open) requestAnimationFrame(() => {
      const box = toggle.current?.getBoundingClientRect();
      if (box && box.top < 0) toggle.current.scrollIntoView({ block: "start" });
    });
  }
  return <div className="brand-menu-section">
    <MenuToggle buttonRef={toggle} open={open} controls={id} name={group.name} onToggle={handleToggle} />
    {open && <div id={id} className="brand-menu-panel"><BrandMenu slug={sample.matched_brand_slug} includePending goals={goals} /></div>}
  </div>;
}

export default function BrandDirectory({ pins, onLocate, isFavorite, onToggleFavorite, goals, sortOrder = "az", openMenus, onToggleMenu }) {
  const [visible, setVisible] = useState(3);
  // Expansion lives with the caller (in memory) so filters and refreshes keep it; this fallback
  // serves standalone use.
  const [localOpen, setLocalOpen] = useState(() => new Set());
  const toggleLocal = useCallback(key => setLocalOpen(current => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  }), []);
  const expanded = openMenus ?? localOpen;
  const toggleMenu = onToggleMenu ?? toggleLocal;
  const groups = groupByBrand(pins).sort((a,b) => sortOrder === "nearest" ? a.pins[0].distance_km - b.pins[0].distance_km : sortOrder === "za" ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name));
  return <><div className="brand-directory">{groups.slice(0, visible).map(group => {
    const sample = group.pins[0];
    const photo = brandPhoto(group.key);
    const menuKey = branchKey(sample);
    const reviewed = group.pins.some(pin => pin.has_calorie_data);
    const itemCount = sample.catalog_item_count ?? sample.item_count;
    const rows = list => <ul className="brand-branch-list">{list.map(pin => <BranchRow key={branchKey(pin)} pin={pin} onLocate={onLocate} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />)}</ul>;
    return <article className="brand-group" key={group.key}>
      <header className="brand-group-heading">{photo ? <a href={photo.source} target="_blank" rel="noreferrer" className="brand-photo-link"><img src={photo.url} alt={photo.alt} loading="lazy" /><span>Source photo ↗</span></a>
        : null}
        <div><h3>{group.name}</h3><p className="brand-group-meta">
          {itemCount ? <span>{itemCount} menu {itemCount === 1 ? "item" : "items"}</span> : null}
          <span>{reviewed ? "Includes reviewed nutrition" : "Menu awaiting review"}</span>
          {group.pins.length > 1 && <span>{group.pins.length} branches nearby</span>}
        </p></div>
      </header>
      {rows(group.pins.slice(0, 1))}
      {group.pins.length > 1 && <details className="brand-more"><summary>View {group.pins.length - 1} more {group.pins.length === 2 ? "branch" : "branches"}</summary>{rows(group.pins.slice(1))}</details>}
      {sample.matched_brand_slug && <BrandMenuSection group={group} menuKey={menuKey} open={expanded.has(menuKey)} onToggle={toggleMenu} goals={goals} />}
    </article>;
  })}</div>{visible < groups.length && <button type="button" className="button-secondary more-brands" onClick={() => setVisible(value => value + 3)}>Show more restaurants ({groups.length - visible} remaining)</button>}</>;
}
