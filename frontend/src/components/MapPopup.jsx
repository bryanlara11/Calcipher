import MealRating from "./MealRating.jsx";
import { useId, useState } from "react";
import { useBrandMenu } from "../menuCache.js";
import { formatCalories, formatGrams, knownMacros } from "../macros.js";
import GoalBadge from "./GoalBadge.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import SourcePhoto from "./SourcePhoto.jsx";
import AddToPlan from "./AddToPlan.jsx";

const SOURCE_LABELS = { official: "Official nutrition", community_reported: "Community-reported nutrition", estimated: "Estimated nutrition" };
const hasWebSource = url => /^https?:\/\//i.test(url || "");
const isInstagramPost = url => /^https:\/\/(www\.)?instagram\.com\//i.test(url || "");

// One native button for both states. Its visible text stays short; screen readers also hear which
// restaurant it belongs to, and aria-expanded reports the state. Enter/Space come from <button>.
export function MenuToggle({ open, controls, name, onToggle, className = "menu-toggle", buttonRef }) {
  const label = open ? "Hide menu" : "View menu";
  return <button ref={buttonRef} type="button" className={className} aria-label={`${label} for ${name}`} aria-expanded={open} aria-controls={controls} onClick={onToggle}>
    {label}
  </button>;
}

export function BrandMenu({ slug, includePending = false, goals }) {
  const state = useBrandMenu(slug, includePending);
  if (state.loading) return <p className="text-sm text-slate-mute" role="status">Loading menu…</p>;
  if (state.error) return <p className="text-sm text-warn" role="alert">{state.error}</p>;
  if (!state.items.length) return <p className="text-sm text-slate-mute">No menu records available.</p>;
  return <div className="branch-menu"><MenuRecords items={state.items} goals={goals} />
    <p className="branch-availability">Brand nutrition. Item availability at this branch is unconfirmed.</p>
  </div>;
}

// `ranked` keeps the server's order as a numbered list and names each item's restaurant, for
// cross-restaurant rankings such as "highest protein".
export function MenuRecords({ items, goals, ranked = false, label = "Menu nutrition records" }) {
  const List = ranked ? "ol" : "ul";
  return <List className={`menu-records${ranked ? " menu-records-ranked" : ""}`} aria-label={label}>
      {items.map((item, index) => <li key={item.item_id} className="menu-record">
        <SourcePhoto item={item} />
        <div className="menu-record-heading">
          <h3>{ranked && <span className="menu-record-rank num">{index + 1}.</span>}{item.food_item}{ranked && <small className="menu-record-brand">{item.brand_name}</small>}</h3>
          <p className="menu-energy"><strong className="num">{formatCalories(item)}</strong><span>kcal</span></p>
        </div>
        <div className="menu-record-status"><span className="review-pill">{item.is_verified ? "Checked against source" : "Pending review"}</span></div>
        <GoalBadge item={item} goals={goals} />
        <p className="menu-serving">{item.serving_size || "Serving size unavailable"}</p>
        {knownMacros(item).length > 0 && <dl className="menu-macros">{knownMacros(item).map(([label, value]) =>
          <div key={label}><dt>{label}</dt><dd className="num">{formatGrams(value)}</dd></div>)}
        </dl>}
        <p className="menu-price">{item.price_php == null ? "Price unavailable" : `Source price ₱${item.price_php.toFixed(2)}`}</p>
        <MealRating item={item} /><AddToPlan item={item} />
        <footer className="menu-evidence">
          <p>{SOURCE_LABELS[item.source_classification] || "Source classification unavailable"}</p>
          <div>{hasWebSource(item.nutrition_source_url || item.source_post_url) && <a href={item.nutrition_source_url || item.source_post_url} target="_blank" rel="noreferrer">Nutrition source ↗</a>}
            {item.nutrition_source_url && isInstagramPost(item.source_post_url) && item.source_post_url !== item.nutrition_source_url && <a href={item.source_post_url} target="_blank" rel="noreferrer">Instagram brand post ↗</a>}
            {item.collected_at && <time dateTime={item.collected_at}>Collected {item.collected_at}</time>}
          </div>
        </footer>
      </li>)}
    </List>;
}

export function BranchPopup({ pin, goals, isFavorite, onToggleFavorite }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  return <div className="branch-popup">
    <div className="branch-heading"><h2 className="branch-name">{pin.poi_name}</h2><FavoriteButton pin={pin} saved={isFavorite?.(pin)} onToggle={onToggleFavorite} /></div>
    <p className="branch-location">{pin.distance_km.toFixed(2)} km away{pin.address_hint ? ` · ${pin.address_hint}` : ""}</p>
    {pin.has_catalog_data || pin.has_calorie_data ? <>
      <p className="branch-menu-count">{pin.catalog_item_count || pin.item_count} menu items
        {pin.has_calorie_data && <> · reviewed items from <strong className="num">{pin.min_calories} kcal</strong></>}
      </p>
      {pin.pending_item_count > 0 && <p className="branch-review-note">{pin.pending_item_count} pending source review</p>}
      <MenuToggle open={menuOpen} controls={menuId} name={pin.poi_name} className="popup-menu-toggle" onToggle={() => setMenuOpen(value => !value)} />
      {menuOpen && <div id={menuId}><BrandMenu slug={pin.matched_brand_slug} includePending={Boolean(pin.has_catalog_data)} goals={goals} /></div>}
    </> : <p className="branch-review-note">No reviewed calorie data is available for this restaurant yet.</p>}
  </div>;
}
