import { createContext, useContext, useEffect, useState } from "react";

export const PLAN_KEY = "calcipher.mealPlan.v1";
export const MEAL_SLOTS = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };
export const MealPlanContext = createContext(null);
export const useMealPlanContext = () => useContext(MealPlanContext);
export function today() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Manila", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date()).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function validDay(day) {
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const date = new Date(`${day}T12:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === day;
}
function validItem(item) {
  return item && typeof item.item_id === "string" && typeof item.food_item === "string"
    && typeof item.brand_name === "string" && Number.isFinite(item.calories) && item.calories >= 0 && item.calories <= 10000;
}
function parse(value) {
  try {
    const rows = JSON.parse(value || "[]");
    return Array.isArray(rows) ? rows.filter(row => typeof row?.id === "string" && validDay(row.day) && MEAL_SLOTS[row.slot] && validItem(row.item)) : [];
  } catch { return []; }
}
export function nutritionTotals(entries, field) {
  return entries.reduce((total, row) => {
    const value = row.item[field];
    return value == null || !Number.isFinite(value) ? { ...total, missing: total.missing + 1 }
      : { ...total, known: total.known + value };
  }, { known: 0, missing: 0 });
}
export function useMealPlan() {
  const [entries, setEntries] = useState(() => { try { return parse(localStorage.getItem(PLAN_KEY)); } catch { return []; } });
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  useEffect(() => {
    try { localStorage.setItem(PLAN_KEY, JSON.stringify(entries)); setStorageUnavailable(false); }
    catch { setStorageUnavailable(true); }
  }, [entries]);
  const add = (item, day, slot) => {
    if (!validDay(day) || !MEAL_SLOTS[slot] || !validItem(item)) return false;
    const safe = Object.fromEntries(["item_id", "food_item", "brand_name", "calories", "protein_g", "carbs_g", "fat_g", "serving_size", "is_verified", "source_post_url", "source_images"].map(key => [key, item[key]]));
    setEntries(current => current.some(row => row.day === day && row.slot === slot && row.item.item_id === item.item_id) ? current
      : [...current, { id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`, day, slot, item: safe }]);
    return true;
  };
  return { entries, add, remove: id => setEntries(current => current.filter(row => row.id !== id)), storageUnavailable };
}
