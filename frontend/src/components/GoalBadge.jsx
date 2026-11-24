export function matchesNutritionGoals(item, goals = {}) {
  const max = goals.max_calories;
  const protein = goals.min_protein_g;
  const hasMax = max != null && max !== "" && Number.isFinite(Number(max)) && Number(max) >= 0;
  const hasProtein = protein != null && protein !== "" && Number.isFinite(Number(protein)) && Number(protein) >= 0;
  if (!hasMax && !hasProtein) return false;
  return (!hasMax || item.calories != null && item.calories <= Number(max))
    && (!hasProtein || item.protein_g != null && item.protein_g >= Number(protein));
}

export default function GoalBadge({ item, goals }) {
  if (!matchesNutritionGoals(item, goals)) return null;
  if (!item.is_verified) return <p className="goal-match-pending">Matches your goals · Pending review</p>;
  return <div className="healthy-badge" title="Matches your selected calorie and protein goals">
    <img src="/brand/approved-seal.webp" alt="CalCipher Approved seal" width="72" height="54" />
    <span><strong>Healthy match</strong><small>Meets your selected nutrition goals</small></span>
  </div>;
}
