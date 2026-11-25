import { useState } from "react";
import { useRatings, useRatingSummary } from "../ratings.js";
export default function MealRating({ item }) {
  const context = useRatings();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rating = useRatingSummary(item);
  if (!context) return null;
  return <div className="meal-rating"><p>{rating.rating_count ? `${Number(rating.rating_average).toFixed(1)}/5 · ${rating.rating_count} community ${rating.rating_count === 1 ? "rating" : "ratings"}` : "No community ratings yet"}</p>
    <details><summary>Rate as a healthy choice</summary><p>Your opinion, from 1 (low) to 5 (high). One rating per browser; you can change it.</p><div className="rating-buttons" role="group" aria-label={`Rate ${item.food_item} as a healthy choice`}>{[1,2,3,4,5].map(score=><button type="button" key={score} aria-pressed={rating.ownScore === score} aria-label={`${score} ${score === 1 ? "star" : "stars"}`} disabled={busy} onClick={async()=>{setBusy(true);setError("");try { await context.vote(item.item_id,score); } catch { setError("Rating could not be saved. Try again later."); } finally { setBusy(false); } }}>{score} ★</button>)}</div></details>
    {error && <p role="alert">{error}</p>}
  </div>;
}
