import { useEffect, useRef, useState } from "react";
import { Icon } from "./Brand.jsx";
import GoalBadge from "./GoalBadge.jsx";
import FavoriteButton from "./FavoriteButton.jsx";
import SourcePhoto from "./SourcePhoto.jsx";
import AddToPlan from "./AddToPlan.jsx";

const CHIPS = [
  { label: "Under 500 kcal", text: "something filling under 500 kcal" },
  { label: "30 g+ protein", text: "at least 30g protein" },
  { label: "Walking distance", text: "chicken within 1 km" },
  { label: "Light snack", text: "light snack under 300 kcal" },
  { label: "Grilled, high protein", text: "grilled chicken with high protein under 450 kcal" },
];

const SOURCE_LABELS = { official: "Brand-published nutrition", community_reported: "Community-reported nutrition", estimated: "Estimated nutrition", unknown: "Source type unavailable" };
const fmtG = (v) => (v == null ? "—" : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })} g`);

export function NutritionPanel({ item, index, onLocate, preview = false, goals, isFavorite, onToggleFavorite }) {
  return <article className="nutrition-card">
    <SourcePhoto item={item} />
    <div className="flex items-start justify-between gap-3"><div><p className="eyebrow mb-2">{item.brand_name}</p><h2>{item.food_item}</h2></div>{preview ? <span className="text-sm text-slate-mute">Pending review</span> : <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-mango text-xs font-bold text-white" aria-label={`Map pin ${index}`}>{index}</span>}</div>
    <p className="mt-2 text-xs text-slate-mute">{item.serving_size || "Serving size unavailable"}</p>
    <GoalBadge item={item} goals={goals} />
    {item.source_classification && <p className="mt-1 text-sm text-slate-mute">{SOURCE_LABELS[item.source_classification]}</p>}
    <div className="kcal-row"><strong className="num">{item.calories} <span>kcal</span></strong><span>Calories</span></div>
    <dl className="nutrition-macros">{[["Protein", item.protein_g], ["Carbs", item.carbs_g], ["Fat", item.fat_g]].map(([name, value]) => <div key={name}><dt>{name}</dt><dd className="num">{fmtG(value)}</dd></div>)}</dl>
    <div className="price-note">{item.price_php == null ? "Price unavailable" : `Source price ₱${item.price_php.toFixed(2)}`}
      {item.price_php != null && <small>Branch prices may vary. {/^https?:\/\//i.test(item.price_source_url || "") && <a className="underline" href={item.price_source_url} target="_blank" rel="noreferrer">Price source</a>}</small>}
    </div>
    <div className="mt-3 flex items-center justify-between gap-2 text-xs"><button type="button" className="flex min-w-0 items-center gap-1 text-left font-bold text-ube" onClick={() => onLocate(item)}><Icon name="pin" size={14} />{item.poi_name}</button><span className="num shrink-0 text-slate-mute">{item.distance_km.toFixed(2)} km</span><FavoriteButton pin={item} saved={isFavorite?.(item)} onToggle={onToggleFavorite} /></div>
    {item.place_id && <p className="mt-2 text-[10px] text-slate-mute"><a className="underline" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/search/?${new URLSearchParams({ api: "1", query: item.poi_name, query_place_id: item.place_id })}`}>View branch on Google Maps</a>{item.attributions?.map(a => <span key={a.provider}> · {a.provider}</span>)}</p>}
    <AddToPlan item={item} />
    <p className="verification-note">{item.is_verified ? "Checked against source." : preview ? "Pending review; source values are not approved." : "Unverified; may contain OCR errors."} {/^https?:\/\//i.test(item.source_post_url || "") && <a className="underline" href={item.source_post_url} target="_blank" rel="noreferrer">Nutrition source</a>} {item.collected_at && <span>Collected {item.collected_at}. </span>}{/^https?:\/\//i.test(item.secondary_source_url || "") && <a className="underline" href={item.secondary_source_url} target="_blank" rel="noreferrer">Collection source</a>}</p>
    <p className="mt-2 text-xs text-slate-mute">Brand nutrition; availability at this branch is not confirmed.</p>
  </article>;
}

function Message({ msg, onLocate, searchContext, isFavorite, onToggleFavorite }) {
  if (msg.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-ink px-4 py-3 text-label text-xs">
        <p className="whitespace-pre-wrap break-words">{msg.text}</p>
      </div>
    );
  }
  if (msg.role === "error") {
    return (
      <div className="border-l-4 border-warn bg-label px-3 py-2 text-sm" role="alert">
        <p className="font-bold text-warn">{msg.title}</p>
        <p>{msg.text}</p>
      </div>
    );
  }
  const { response } = msg;
  const previousArea = msg.searchContext && searchContext && ["lat", "lng", "radiusKm", "provider"].some(key => msg.searchContext[key] !== searchContext[key]);
  return <div className="space-y-3">
    {previousArea && <p className="text-sm text-slate-mute" role="status">Earlier search area. Search again to use your current pin and radius.</p>}
    <MealResults response={response} onLocate={onLocate} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />
  </div>;
}

export function MealResults({ response, onLocate, isFavorite, onToggleFavorite }) {
  const [notice, ...rest] = response.relaxation.applied ? response.answer.split("\n\n") : [null, response.answer];
  const body = rest.join("\n\n");
  return (
    <div className="space-y-3">
      {notice && <p className="border-l-4 border-mango bg-mango-soft px-3 py-2 text-sm">{notice}</p>}
      {response.results.length === 0 ? (
        <p className="whitespace-pre-wrap">{body}</p>
      ) : (
        <>
          {response.inspector.generator_used === "llm" && <p className="whitespace-pre-wrap">{body}</p>}
          <div className="meal-result-grid">
            {response.results.map((item, i) => (
              <NutritionPanel key={item.item_id} item={item} index={i + 1} onLocate={onLocate} goals={response.inspector?.constraints} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />
            ))}
          </div>
        </>
      )}
      {response.catalog_matches?.length > 0 && <section aria-label="Catalog matches pending review" className="space-y-3">
        <h2>Catalog matches · pending review</h2>
        <div className="meal-result-grid">{response.catalog_matches.map(item => <NutritionPanel key={item.item_id} item={item} preview onLocate={onLocate} goals={response.inspector?.constraints} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />)}</div>
      </section>}
    </div>
  );
}

export default function ChatPanel({ messages, onSend, busy, onLocate, searchContext, isFavorite, onToggleFavorite }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  function submit(text) {
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setDraft("");
  }

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Chat">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4" aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty"><span className="assistant-mark"><Icon name="leaf" size={24} /></span><h2>Search for a meal</h2><p>Enter a food and any calorie, protein, or budget limits.</p><p className="mt-3">Example: “chicken under 500 kcal and PHP150”.</p></div>
        )}
        {messages.map((m) => (
          <Message key={m.id} msg={m} onLocate={onLocate} searchContext={searchContext} isFavorite={isFavorite} onToggleFavorite={onToggleFavorite} />
        ))}
        {busy && <p className="text-sm text-slate-mute">Checking nearby restaurants…</p>}
        <div ref={endRef} />
      </div>

      <div className="chat-composer">
        <div className="-mx-1 mb-3 flex gap-2 overflow-x-auto px-1 pb-1">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              type="button"
              disabled={busy}
              onClick={() => submit(c.text)}
              className="prompt-chip"
            >
              {c.label}
            </button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft);
          }}
        >
          <label htmlFor="chat-input" className="sr-only">
            Describe what you want to eat
          </label>
          <input
            id="chat-input"
            value={draft}
            maxLength={500}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. siomai or noodles under 400 kcal"
            className="chat-input"
          />
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            className="button-primary"
          >
            Search
          </button>
        </form>
        <p className="composer-note">Check nutrition and prices against the listed sources.</p>
      </div>
    </section>
  );
}
