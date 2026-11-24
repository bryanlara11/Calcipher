import { useState } from "react";
import { MEAL_SLOTS, nutritionTotals, today, useMealPlanContext } from "../mealPlan.js";
import { Icon } from "./Brand.jsx";
import SourcePhoto from "./SourcePhoto.jsx";

export default function MealPlanPage({ headingRef }) {
  const plan = useMealPlanContext();
  const [day, setDay] = useState(today);
  const entries = plan.entries.filter(entry => entry.day === day);
  const pending = entries.filter(entry => !entry.item.is_verified).length;
  return <section className="meal-plan-page"><div className="page-title-row"><div><h1 ref={headingRef} tabIndex={-1}>CalCipher meal plan</h1><p className="page-lead">Build your day from saved menu records. Your plan stays on this device.</p></div><label className="plan-day">Plan date<input aria-label="Meal plan date" type="date" value={day} onChange={event => setDay(event.target.value)} /></label></div>
    {plan.storageUnavailable && <p role="status">Your browser cannot save this plan between visits. It remains available during this visit.</p>}
    <dl className="plan-totals">{[["Calories", "calories", "kcal"], ["Protein", "protein_g", "g"], ["Carbs", "carbs_g", "g"], ["Fat", "fat_g", "g"]].map(([label, field, unit]) => {
      const total = nutritionTotals(entries, field);
      return <div key={field}><dt>{label}</dt><dd className="num">{entries.length && total.missing === entries.length ? "Unavailable" : `${Number(total.known.toFixed(2))} ${unit}`}</dd>{total.missing > 0 && <small>{total.missing} {total.missing === 1 ? "meal has" : "meals have"} no {label.toLowerCase()} value</small>}</div>;
    })}</dl>
    {pending > 0 && <p className="plan-review-note">{pending} planned {pending === 1 ? "meal is" : "meals are"} pending source review. These are browsing choices, not approved recommendations.</p>}
    <div className="plan-slots">{Object.entries(MEAL_SLOTS).map(([key, label]) => {
      const meals = entries.filter(entry => entry.slot === key);
      return <section className="plan-slot" key={key}><h2>{label}</h2>{meals.length ? meals.map(entry => <article className="planned-meal" key={entry.id}>
        <SourcePhoto item={entry.item} /><div className="planned-meal-heading"><div><h3>{entry.item.food_item}</h3><p>{entry.item.brand_name}</p></div><button type="button" className="icon-button" aria-label={`Remove ${entry.item.food_item} from meal plan`} onClick={() => plan.remove(entry.id)}><Icon name="close" size={18} /></button></div>
        <p className="num">{entry.item.calories} kcal · {entry.item.protein_g == null ? "Protein unavailable" : `${entry.item.protein_g} g protein`}</p>
        <p className="planned-meal-status">{entry.item.is_verified ? "Reviewed source record" : "Pending review"} · {entry.item.serving_size || "Serving size unavailable"}</p>
        {/^https?:\/\//i.test(entry.item.source_post_url || "") && <a href={entry.item.source_post_url} target="_blank" rel="noreferrer">Nutrition source ↗</a>}
      </article>) : <div className="plan-slot-empty"><p>No {label.toLowerCase()} saved yet.</p><a href="#/search">Find a meal<Icon name="arrow" size={15} /></a></div>}</section>;
    })}</div>
  </section>;
}
