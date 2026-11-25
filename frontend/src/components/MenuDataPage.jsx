import { useEffect, useState } from "react";
import { catalogSummary } from "../api/client.js";
import { Icon } from "./Brand.jsx";

export default function MenuDataPage({ headingRef }) {
  const [catalog, setCatalog] = useState(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    catalogSummary({ signal: controller.signal }).then(setCatalog).catch(error => {
      if (error.name !== "AbortError") setUnavailable(true);
    });
    return () => controller.abort();
  }, []);
  return <section className="home-catalog menu-data-page">
    <a href="#/home" className="data-back-link"><Icon name="home" size={16} />Back to Home</a>
    <h1 ref={headingRef} tabIndex={-1}>About menu data</h1>
    <p>Restaurant brands are selected from @anongcaloriesnito’s Instagram posts. Nutrition may come from those posts, official menus or clearly labelled web transcriptions. Each record keeps its brand-origin post and nutrition source; brands without that Instagram origin are excluded.</p>
    {catalog ? <dl className="catalog-stats">{[["Menu records", catalog.records], ["Brands", catalog.brands], ["Reviewed", catalog.reviewed], ["Pending review", catalog.pending]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="num">{value}</dd></div>)}</dl>
      : <p className="catalog-status" role="status">{unavailable ? "The live catalog summary is unavailable. Source links remain attached to menu records." : "Loading the catalog summary…"}</p>}
    <div className="catalog-notes"><article><h2>Review status stays visible</h2><p>Pending records are available to browse. Recommendations use reviewed records only; a community report remains a community report after review.</p></article><article><h2>Unknown means unavailable</h2><p>Missing macros, servings and prices are never filled in with guesses. Budget searches exclude records without a checked price.</p></article><article><h2>Check the branch</h2><p>Brand nutrition does not guarantee that every branch has the same item or price. Confirm availability when you order.</p></article></div>
    <div className="catalog-source-links"><a href="https://www.instagram.com/anongcaloriesnito/" target="_blank" rel="noreferrer">Community source ↗</a><a href="https://worththecals.com/methodology" target="_blank" rel="noreferrer">worththecals methodology ↗</a></div>
  </section>;
}
