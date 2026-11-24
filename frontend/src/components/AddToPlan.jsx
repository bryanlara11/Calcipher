import { useState } from "react";
import { MEAL_SLOTS, today, useMealPlanContext } from "../mealPlan.js";

export default function AddToPlan({ item }) {
  const plan = useMealPlanContext();
  const [day, setDay] = useState(today);
  const [slot, setSlot] = useState("lunch");
  const [saved, setSaved] = useState(false);
  if (!plan) return null;
  return <details className="plan-add"><summary>Add to meal plan</summary>
    <form onSubmit={event => { event.preventDefault(); setSaved(plan.add(item, day, slot)); }}>
      <label>Date<input aria-label={`Plan date for ${item.food_item}`} type="date" required value={day} onChange={event => { setDay(event.target.value); setSaved(false); }} /></label>
      <label>Meal<select aria-label={`Meal slot for ${item.food_item}`} value={slot} onChange={event => { setSlot(event.target.value); setSaved(false); }}>{Object.entries(MEAL_SLOTS).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <button type="submit" className="button-secondary">Save meal</button>
    </form>
    {saved && <p role="status">Saved for {MEAL_SLOTS[slot].toLowerCase()}. <a href="#/plan">View meal plan</a></p>}
  </details>;
}
