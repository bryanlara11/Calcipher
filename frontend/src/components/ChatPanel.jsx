import { useEffect, useRef, useState } from "react";

const CHIPS = [
  { label: "Under 500 kcal", text: "something filling under 500 kcal" },
  { label: "30 g+ protein", text: "at least 30g protein" },
  { label: "Walking distance", text: "chicken within 1 km" },
  { label: "Light snack", text: "light snack under 300 kcal" },
  { label: "Grilled, high protein", text: "grilled chicken with high protein under 450 kcal" },
];

const fmtG = (v) => (v == null ? "—" : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} g`);

// The signature element: each result is a miniature Nutrition Facts panel.
function NutritionPanel({ item, index, onLocate }) {
  return (
    <article className="border-2 border-ink bg-label px-3 pb-2 pt-1.5">
      <header className="flex items-start justify-between gap-2">
        <h3 className="type-label text-[1.35rem] leading-[1.05]">{item.food_item}</h3>
        <span
          className="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-full border-2 border-ink bg-mango text-xs font-black"
          aria-label={`Map pin ${index}`}
        >
          {index}
        </span>
      </header>
      <p className="text-sm leading-snug">
        {item.brand_name}, {item.serving_size}
      </p>
      <div className="rule-heavy mt-1.5 flex items-end justify-between pt-0.5">
        <span className="type-label text-lg">Calories</span>
        <span className="type-label num text-[2.4rem] leading-none">{item.calories}</span>
      </div>
      <dl className="rule-mid mt-1 text-sm">
        {[
          ["Protein", item.protein_g],
          ["Carbohydrates", item.carbs_g],
          ["Total fat", item.fat_g],
        ].map(([k, v]) => (
          <div key={k} className="rule-thin flex justify-between py-0.5 first:border-t-0">
            <dt className="font-bold">{k}</dt>
            <dd className="num">{fmtG(v)}</dd>
          </div>
        ))}
      </dl>
      <div className="rule-heavy mt-1 flex items-center justify-between gap-2 pt-1.5 text-sm">
        <button
          type="button"
          onClick={() => onLocate(item)}
          className="min-w-0 truncate text-left font-semibold text-ube underline decoration-2 underline-offset-2 hover:text-ink"
        >
          {item.poi_name}
        </button>
        <span className="num shrink-0 type-cond font-bold">{item.distance_km.toFixed(2)} km</span>
      </div>
      {item.place_id && (
        <p className="mt-1 text-xs text-slate-mute">
          <a className="underline" target="_blank" rel="noreferrer"
            href={`https://www.google.com/maps/search/?${new URLSearchParams({
              api: "1", query: item.poi_name, query_place_id: item.place_id,
            })}`}>View branch on Google Maps</a>
          {item.attributions?.map(a => <span key={a.provider}> · {a.provider}</span>)}
        </p>
      )}
      {!item.is_verified && (
        <p className="mt-1 text-xs text-slate-mute">Community estimate, not yet verified.</p>
      )}
    </article>
  );
}

function Message({ msg, onLocate }) {
  if (msg.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-md bg-ink px-3 py-2 text-label">
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
          <div className="grid gap-3">
            {response.results.map((item, i) => (
              <NutritionPanel key={item.item_id} item={item} index={i + 1} onLocate={onLocate} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function ChatPanel({ messages, onSend, busy, onLocate }) {
  const [draft, setDraft] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
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
          <div className="space-y-2 text-[0.95rem] leading-relaxed text-slate-mute">
            <p className="text-ink">
              Ask for a meal and a limit. CalCipher only suggests items from restaurants that are actually
              on the map around your pin.
            </p>
            <p>Try “grilled chicken under 450 kcal within 2 km” or “30g protein at Mang Inasal”.</p>
          </div>
        )}
        {messages.map((m) => (
          <Message key={m.id} msg={m} onLocate={onLocate} />
        ))}
        {busy && <p className="text-sm text-slate-mute">Checking nearby restaurants…</p>}
        <div ref={endRef} />
      </div>

      <div className="border-t-2 border-ink bg-label px-4 pb-4 pt-3">
        <div className="-mx-1 mb-3 flex gap-2 overflow-x-auto px-1 pb-1">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              type="button"
              disabled={busy}
              onClick={() => submit(c.text)}
              className="shrink-0 rounded-full border-2 border-ink px-3 py-1 text-sm font-semibold hover:bg-ink hover:text-label disabled:opacity-40"
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
            className="min-w-0 flex-1 rounded-md border-2 border-ink bg-label px-3 py-2 placeholder:text-slate-mute focus:outline-none focus-visible:ring-2 focus-visible:ring-mango"
          />
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            className="rounded-md bg-ube px-4 py-2 font-bold text-label hover:bg-ink disabled:opacity-40"
          >
            Search
          </button>
        </form>
      </div>
    </section>
  );
}
