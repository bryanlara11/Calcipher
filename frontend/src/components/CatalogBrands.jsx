import { useEffect, useState } from "react";
import { catalogBrands } from "../api/client.js";
import { BrandMenu } from "./MapPopup.jsx";

function BrandPicker({ goals }) {
  const [brands, setBrands] = useState([]);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState("Loading brands…");
  useEffect(() => {
    const controller = new AbortController();
    catalogBrands({ signal: controller.signal }).then(data => {
      if (!Array.isArray(data.brands)) throw new Error("Invalid brand list");
      setBrands(data.brands); setStatus(data.brands.length ? "" : "No brands available yet.");
    }).catch(error => { if (error.name !== "AbortError") setStatus("Brand menus could not load. Please try again later."); });
    return () => controller.abort();
  }, []);
  return <div className="catalog-brand-picker"><p>Browse source menus from any area. Check Nearby for branch locations.</p>
    {status && <p role="status">{status}</p>}
    <label className="field">Restaurant brand<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">Choose a brand</option>{brands.map(brand => <option value={brand.brand_slug} key={brand.brand_slug}>{brand.brand_name} · {brand.records} menu {brand.records === 1 ? "item" : "items"}</option>)}</select></label>
    {selected && <BrandMenu key={selected} slug={selected} includePending goals={goals} />}
  </div>;
}
export default function CatalogBrands({ goals }) {
  const [open, setOpen] = useState(false);
  return <details className="catalog-brand-browser" onToggle={event => setOpen(event.currentTarget.open)}><summary>Browse all brands</summary>{open && <BrandPicker goals={goals} />}</details>;
}
