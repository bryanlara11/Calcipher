import { createContext, useContext, useEffect, useState } from "react";

export const TARGETS_KEY = "calcipher.planTargets.v1";
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
    && typeof item.brand_name === "string" && Number.isFinite(item.calories) && item.calories >= 0 && item.calories <= 10000
    && (item.calories_min == null || Number.isFinite(item.calories_min) && item.calories_min >= 0 && item.calories_min <= item.calories);
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
export function calorieTotals(entries) {
  const upper = entries.reduce((sum, { item }) => sum + item.calories, 0);
  const lower = entries.reduce((sum, { item }) => sum + (item.calories_min ?? item.calories), 0);
  return { calories: Number(upper.toFixed(6)), calories_min: Number(lower.toFixed(6)) };
}
export function useMealPlan(notify) {
  const [entries, setEntries] = useState(() => { try { return parse(localStorage.getItem(PLAN_KEY)); } catch { return []; } });
  const [targets, setTargets] = useState(() => { try { const saved = JSON.parse(localStorage.getItem(TARGETS_KEY) || "{}"); return Object.fromEntries(Object.entries(saved).filter(([day,value]) => validDay(day) && Number.isInteger(value) && value > 0 && value <= 10000)); } catch { return {}; } });
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  useEffect(() => {
    try { localStorage.setItem(PLAN_KEY, JSON.stringify(entries)); localStorage.setItem(TARGETS_KEY, JSON.stringify(targets)); setStorageUnavailable(false); }
    catch { setStorageUnavailable(true); }
  }, [entries, targets]);
  const add = (item, day, slot) => {
    if (!validDay(day) || !MEAL_SLOTS[slot] || !validItem(item)) return false;
    const matches = row => row.day === day && row.slot === slot && row.item.item_id === item.item_id;
    if (entries.some(matches)) {
      notify?.(`${item.food_item} is already in your meal plan for ${slot}.`);
      return true;
    }
    const safe = Object.fromEntries(["item_id", "food_item", "brand_name", "calories", "calories_min", "protein_g", "carbs_g", "fat_g", "serving_size", "menu_note", "is_verified", "source_post_url", "nutrition_source_url", "source_images"].map(key => [key, item[key]]));
    setEntries(current => current.some(matches) ? current
      : [...current, { id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`, day, slot, item: safe }]);
    notify?.(`Added ${item.food_item} to your meal plan for ${slot}.`);
    return true;
  };
  const remove = id => {
    const entry = entries.find(row => row.id === id);
    if (!entry) return;
    setEntries(current => current.filter(row => row.id !== id));
    notify?.(`Removed ${entry.item.food_item} from your meal plan.`);
  };
  const setTarget = (day, value) => {
    if (!validDay(day) || value !== "" && (!Number.isInteger(Number(value)) || Number(value) < 1 || Number(value) > 10000)) return;
    setTargets(current => { const next = { ...current }; if (value === "") delete next[day]; else next[day] = Number(value); return next; });
  };
  return { entries, targets, setTarget, add, remove, storageUnavailable };
}

export function weekDays(day) {
  if (!validDay(day)) return [];
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return Array.from({ length: 7 }, (_, index) => { const next = new Date(date); next.setUTCDate(next.getUTCDate() + index); return next.toISOString().slice(0,10); });
}
export function shiftDay(day, amount) {
  const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + amount); return date.toISOString().slice(0,10);
}
