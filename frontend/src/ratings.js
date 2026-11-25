import { createContext, useContext, useState } from "react";
import { rateMeal } from "./api/client.js";
export const RatingsContext = createContext(null);
export const useRatings = () => useContext(RatingsContext);
let visitVoter;
function voterId() {
  if (visitVoter) return visitVoter;
  try { visitVoter = localStorage.getItem("calcipher.ratingIdentity.v1"); } catch { /* this visit */ }
  if (!/^[0-9a-f-]{36}$/i.test(visitVoter || "")) visitVoter = crypto.randomUUID();
  try { localStorage.setItem("calcipher.ratingIdentity.v1", visitVoter); } catch { /* this visit */ }
  return visitVoter;
}
export function useMealRatings() {
  const [ratings, setRatings] = useState({});
  async function vote(itemId, score) {
    const summary = await rateMeal(itemId, voterId(), score);
    setRatings(current => ({ ...current, [itemId]: { ...summary, ownScore: score } }));
  }
  return { ratings, vote };
}

export function useRatingSummary(item) {
  const context = useRatings();
  const cached = context?.ratings[item.item_id];
  const time = value => Date.parse(value || "") || 0;
  const data = cached && time(cached.rating_updated_at) >= time(item.rating_updated_at) ? cached : item;
  return { ...data, ownScore: cached?.ownScore };
}
