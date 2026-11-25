import MealRating from "./MealRating.jsx";
import { useEffect, useState } from "react";
import { brandItems } from "../api/client.js";
import GoalBadge from "./GoalBadge.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import SourcePhoto from "./SourcePhoto.jsx";
import AddToPlan from "./AddToPlan.jsx";

const SOURCE_LABELS = { official: "Official nutrition", community_reported: "Community-reported nutrition", estimated: "Estimated nutrition" };
const grams = value => value == null ? "Unavailable" : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })} g`;
const hasWebSource = url => /^https?:\/\//i.test(url || "");

export function BrandMenu({ slug, includePending = false, goals }) {
  const [state, setState] = useState({ loading: true, items: [], error: null });
  useEffect(() => {
    const ctrl = new AbortController();
    brandItems(slug, { signal: ctrl.signal, includePending })
      .then(r => {
        if (!Array.isArray(r.items)) throw new Error("Menu details could not be loaded.");
        setState({ loading: false, items: r.items, error: null });
      })
      .catch(e => e.name !== "AbortError" && setState({ loading: false, items: [], error: e.message }));
    return () => ctrl.abort();
  }, [slug, includePending]);

  if (state.loading) return <p className="text-sm text-slate-mute">Loading menu…</p>;
  if (state.error) return <p className="text-sm text-warn">{state.error}</p>;
  if (!state.items.length) return <p className="text-sm text-slate-mute">No menu records available.</p>;
  return <div className="branch-menu"><MenuRecords items={state.items} goals={goals} />
    <p className="branch-availability">Brand nutrition. Item availability at this branch is unconfirmed.</p>
  </div>;
}

export function MenuRecords({ items, goals }) {
  return <ul className="menu-records" aria-label="Menu nutrition records">
      {items.map(item => <li key={item.item_id} className="menu-record">
        <SourcePhoto item={item} />
        <div className="menu-record-heading">
          <h3>{item.food_item}</h3>
          <p className="menu-energy"><strong className="num">{item.calories}</strong><span>kcal</span></p>
        </div>
        <div className="menu-record-status"><span className="review-pill">{item.is_verified ? "Checked against source" : "Pending review"}</span></div>
        <GoalBadge item={item} goals={goals} />
        <p className="menu-serving">{item.serving_size || "Serving size unavailable"}</p>
        <dl className="menu-macros">{[["Protein", item.protein_g], ["Carbs", item.carbs_g], ["Fat", item.fat_g]].map(([label, value]) =>
          <div key={label}><dt>{label}</dt><dd className="num">{grams(value)}</dd></div>)}
        </dl>
        <p className="menu-price">{item.price_php == null ? "Price unavailable" : `Source price ₱${item.price_php.toFixed(2)}`}</p>
        <MealRating item={item} /><AddToPlan item={item} />
        <footer className="menu-evidence">
          <p>{SOURCE_LABELS[item.source_classification] || "Source classification unavailable"}</p>
          <div>{hasWebSource(item.nutrition_source_url || item.source_post_url) && <a href={item.nutrition_source_url || item.source_post_url} target="_blank" rel="noreferrer">Nutrition source ↗</a>}
            {item.nutrition_source_url && hasWebSource(item.source_post_url) && <a href={item.source_post_url} target="_blank" rel="noreferrer">Instagram brand post ↗</a>}
            {item.collected_at && <time dateTime={item.collected_at}>Collected {item.collected_at}</time>}
          </div>
        </footer>
      </li>)}
    </ul>;
}

export function BranchPopup({ pin, goals, isFavorite, onToggleFavorite }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return <div className="branch-popup">
    <div className="branch-heading"><h2 className="branch-name">{pin.poi_name}</h2><FavoriteButton pin={pin} saved={isFavorite?.(pin)} onToggle={onToggleFavorite} /></div>
    <p className="branch-location">{pin.distance_km.toFixed(2)} km away{pin.address_hint ? ` · ${pin.address_hint}` : ""}</p>
    {pin.has_catalog_data || pin.has_calorie_data ? <>
      <p className="branch-menu-count">{pin.catalog_item_count || pin.item_count} menu items
        {pin.has_calorie_data && <> · reviewed items from <strong className="num">{pin.min_calories} kcal</strong></>}
      </p>
      {pin.pending_item_count > 0 && <p className="branch-review-note">{pin.pending_item_count} pending source review</p>}
      <button type="button" className="popup-menu-toggle" aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)}>{menuOpen ? "Hide menu" : "View menu"}</button>
      {menuOpen && <BrandMenu slug={pin.matched_brand_slug} includePending={Boolean(pin.has_catalog_data)} goals={goals} />}
    </> : <p className="branch-review-note">No reviewed calorie data is available for this restaurant yet.</p>}
  </div>;
}
