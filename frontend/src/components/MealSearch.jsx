import { useState } from "react";
import { Icon } from "./Brand.jsx";

export function buildMealQuery({ food, calories, protein, budget }) {
  return [food.trim() || "meal", calories !== "" && `under ${calories} kcal`,
    protein !== "" && `at least ${protein}g protein`, budget !== "" && `under PHP${budget}`].filter(Boolean).join(" ");
}

export default function MealSearch({ onSearch, busy, savedValues, onValuesChange }) {
  const [localValues, setLocalValues] = useState({ food: "", calories: "", protein: "", budget: "" });
  const values = savedValues ?? localValues;
  const setValues = onValuesChange ?? setLocalValues;
  const [expanded, setExpanded] = useState(false);
  const field = name => ({ value: values[name], onChange: event => setValues(v => ({ ...v, [name]: event.target.value })) });
  return <form className="meal-search" onSubmit={event => {
    event.preventDefault();
    if (!busy) onSearch(buildMealQuery(values), Object.fromEntries([
      ["max_calories", values.calories], ["min_protein_g", values.protein], ["max_price_php", values.budget],
    ].filter(([, value]) => value !== "").map(([key, value]) => [key, Number(value)])));
  }}>
    <label className="field food-field"><span>Food or dish</span>
      <div className="input-with-icon"><Icon name="search" size={18} /><input maxLength={300} placeholder="Chicken, noodles, or a snack" {...field("food")} /></div>
    </label>
    <button type="button" className="mobile-filter-button" aria-expanded={expanded} aria-controls="nutrition-filters"
      onClick={() => setExpanded(value => !value)}><Icon name="filter" size={17} /> Nutrition & budget</button>
    <div className="nutrition-filters" id="nutrition-filters" hidden={!expanded}>
      <label className="field"><span>Max calories <small>kcal</small></span><input type="number" min="0" max="10000" step="1" placeholder="No limit" {...field("calories")} /></label>
      <label className="field"><span>Min protein <small>g</small></span><input type="number" min="0" max="500" step="0.01" placeholder="No minimum" {...field("protein")} /></label>
      <label className="field budget-field"><span>Meal budget <small>PHP</small></span><input type="number" min="0" max="99999999" step="0.01" placeholder="Any source price" {...field("budget")} /></label>
      <p className="field-note">Items with no checked price are excluded when you set a budget.</p>
    </div>
    <button type="submit" disabled={busy} className="button-primary search-submit"><Icon name="search" size={18} />{busy ? "Finding meals…" : "Find a meal"}<Icon name="arrow" size={18} /></button>
  </form>;
}
