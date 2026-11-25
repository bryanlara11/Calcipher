import { useRatingSummary } from "../ratings.js";
export function matchesNutritionGoals(item, goals = {}) {
  const max = goals.max_calories;
  const protein = goals.min_protein_g;
  const hasMax = max != null && max !== "" && Number.isFinite(Number(max)) && Number(max) >= 0;
  const hasProtein = protein != null && protein !== "" && Number.isFinite(Number(protein)) && Number(protein) >= 0;
  if (!hasMax && !hasProtein) return false;
  return (!hasMax || item.calories != null && item.calories <= Number(max))
    && (!hasProtein || item.protein_g != null && item.protein_g >= Number(protein));
}

export default function GoalBadge({ item }) {
  const rating = useRatingSummary(item);
  if (!rating.community_pick || rating.rating_count < 3 || rating.rating_average < 4) return null;
  return <div className="healthy-badge" title="Community healthy-choice rating: at least three ratings averaging four stars or higher. This does not verify nutrition values.">
    <img src="/brand/approved-seal.webp" alt="CalCipher community pick seal" width="72" height="54" />
    <span><strong>Community pick</strong><small>{Number(rating.rating_average).toFixed(1)}/5 · {rating.rating_count} ratings{!item.is_verified && " · Nutrition pending review"}</small></span>
  </div>;
}
