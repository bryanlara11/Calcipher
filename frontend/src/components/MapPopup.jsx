import { useEffect, useState } from "react";
import { brandItems } from "../api/client.js";

function BrandMenu({ slug, includePending = false }) {
  const [state, setState] = useState({ loading: true, items: [], error: null });
  useEffect(() => {
    const ctrl = new AbortController();
    brandItems(slug, { signal: ctrl.signal, includePending })
      .then((r) => setState({ loading: false, items: r.items, error: null }))
      .catch((e) => e.name !== "AbortError" && setState({ loading: false, items: [], error: e.message }));
    return () => ctrl.abort();
  }, [slug, includePending]);

  if (state.loading) return <p className="text-sm text-slate-mute">Loading menu…</p>;
  if (state.error) return <p className="text-sm text-warn">{state.error}</p>;
  return (
    <div>
    <p className="mt-2 text-xs text-slate-mute">Brand nutrition. Item availability at this branch is unconfirmed.</p>
    <ul className="mt-2 max-h-48 overflow-y-auto pr-1 text-sm">
      {state.items.map((it) => (
        <li key={it.item_id} className="flex items-baseline justify-between gap-3 border-t border-slate-line py-1">
          <span>{it.food_item}<span className="block text-xs text-slate-mute">
            {it.serving_size || "Serving size unavailable"} · {it.is_verified ? "Checked against source" : "Pending review"}
            {it.price_php == null ? " · Price unavailable" : ` · Source price ₱${it.price_php.toFixed(2)}`}
          </span><span className="block text-xs text-slate-mute">
            {[["Protein", it.protein_g], ["Carbs", it.carbs_g], ["Fat", it.fat_g]]
              .map(([name, value]) => `${name} ${value == null ? "unavailable" : `${value} g`}`).join(" · ")}
          </span><span className="block text-xs text-slate-mute">
            {it.source_classification === "official" ? "Official nutrition" : it.source_classification === "community_reported" ? "Community-reported nutrition" : it.source_classification === "estimated" ? "Estimated nutrition" : "Source classification unavailable"}
            {it.source_post_url && <> · <a href={it.source_post_url} target="_blank" rel="noreferrer">Nutrition source</a></>}
            {it.collected_at && ` · Collected ${it.collected_at}`}
          </span></span>
          <span className="num type-cond whitespace-nowrap font-bold">{it.calories} kcal</span>
        </li>
      ))}
    </ul>
    </div>
  );
}

export function BranchPopup({ pin }) {
  return (
    <div className="w-56">
      <p className="text-base font-bold leading-tight">{pin.poi_name}</p>
      <p className="text-xs text-slate-mute">
        {pin.distance_km.toFixed(2)} km away{pin.address_hint ? `, ${pin.address_hint}` : ""}
      </p>
      {pin.has_catalog_data || pin.has_calorie_data ? (
        <>
          <p className="mt-2 text-sm">
            {pin.catalog_item_count || pin.item_count} {pin.brand_name} menu records
            {pin.has_calorie_data && <> · reviewed items from <strong className="num">{pin.min_calories} kcal</strong></>}
          </p>
          {pin.pending_item_count > 0 && <p className="mt-2 text-xs text-slate-mute">{pin.pending_item_count} records pending review. Pending values are not used in meal recommendations.</p>}
          <BrandMenu slug={pin.matched_brand_slug} includePending={Boolean(pin.has_catalog_data)} />
        </>
      ) : (
        <p className="mt-2 text-sm text-slate-mute">
          No reviewed calorie data is available for this restaurant yet.
        </p>
      )}
    </div>
  );
}
