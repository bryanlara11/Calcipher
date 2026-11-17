const SOURCE_TEXT = {
  live: "Fetched live from Overpass",
  cache: "Served from the 24 h cache",
  stale_cache_fallback: "Overpass failed, used cached branches",
  disabled: "Overpass disabled, cache only",
};

function Row({ k, children }) {
  return (
    <div className="flex justify-between gap-4 border-t border-slate-line py-1 first:border-t-0">
      <dt className="text-slate-mute">{k}</dt>
      <dd className="num text-right font-semibold">{children}</dd>
    </div>
  );
}

function Discovery({ d, title }) {
  return (
    <div>
      <h4 className="mb-1 font-bold">{title}</h4>
      <dl className="text-sm">
        <Row k="Source">{SOURCE_TEXT[d.source] ?? d.source}</Row>
        <Row k="Radius">{(d.radius_m / 1000).toFixed(1)} km</Row>
        <Row k="POIs fetched / matched">
          {d.fetched_count} / {d.matched_count}
        </Row>
        {d.purged_seed_rows > 0 && <Row k="Seed pins replaced">{d.purged_seed_rows}</Row>}
        <Row k="Time">{d.elapsed_ms.toFixed(0)} ms</Row>
        {d.error && <Row k="Error">{d.error}</Row>}
      </dl>
    </div>
  );
}

function Timings({ timings }) {
  const stages = ["parse", "discovery", "embed", "retrieval", "generation"].filter((s) => s in timings);
  const total = timings.total || stages.reduce((a, s) => a + timings[s], 0) || 1;
  const colors = { parse: "#5B2A86", discovery: "#F2A900", embed: "#18202B", retrieval: "#7D8792", generation: "#C9D0D7" };
  return (
    <div>
      <h4 className="mb-1 font-bold">Stage timings ({total.toFixed(0)} ms)</h4>
      <div className="flex h-3 overflow-hidden rounded-sm border border-ink" role="img" aria-label="Stage timing breakdown">
        {stages.map((s) => (
          <div key={s} style={{ width: `${(timings[s] / total) * 100}%`, background: colors[s] }} />
        ))}
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {stages.map((s) => (
          <li key={s} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2" style={{ background: colors[s] }} />
            {s} <span className="num">{timings[s].toFixed(0)} ms</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function RagInspector({ open, onToggle, chat, map }) {
  const insp = chat?.inspector;
  const c = insp?.constraints;
  return (
    <aside
      className={`absolute inset-x-0 bottom-0 z-[1000] border-t-2 border-ink bg-label transition-transform duration-300 ${
        open ? "translate-y-0" : "translate-y-[calc(100%-2.75rem)]"
      }`}
      aria-label="Retrieval inspector"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex h-11 w-full items-center justify-between px-4 text-left font-bold"
      >
        <span>Retrieval inspector</span>
        <span className="text-sm font-normal text-slate-mute">
          {insp ? `${insp.candidate_count} candidates, ${insp.timings_ms.total?.toFixed(0)} ms` : "No search yet"}
          <span aria-hidden="true" className="ml-2 inline-block">{open ? "▾" : "▴"}</span>
        </span>
      </button>

      <div className="grid max-h-[26vh] gap-5 lg:max-h-[45vh] overflow-y-auto px-4 pb-4 sm:grid-cols-2 lg:grid-cols-3">
        {map && <Discovery d={map.discovery} title={`Map layer (${map.matched_count} with data, ${map.unmatched_count} without)`} />}

        {insp ? (
          <>
            {insp.discovery.map((d, i) => (
              <Discovery key={i} d={d} title={i === 0 ? "Chat: restaurant discovery" : "Chat: widened discovery"} />
            ))}
            <div>
              <h4 className="mb-1 font-bold">Parsed constraints ({insp.parser_used})</h4>
              <dl className="text-sm">
                <Row k="Max calories">{c.max_calories ?? "none"}</Row>
                <Row k="Min protein">{c.min_protein_g != null ? `${c.min_protein_g} g` : "none"}</Row>
                <Row k="Max distance">{c.max_distance_km} km</Row>
                <Row k="Brand">{c.target_brand ?? "any"}</Row>
                <Row k="Semantic query">{c.semantic_query || "(empty)"}</Row>
              </dl>
              {insp.parser_warnings.length > 0 && (
                <ul className="mt-1 list-disc pl-5 text-xs text-slate-mute">
                  {insp.parser_warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
            </div>
            <div>
              <h4 className="mb-1 font-bold">Hybrid retrieval</h4>
              <dl className="text-sm">
                <Row k="Rows passing SQL filters">{insp.candidate_count}</Row>
                <Row k="Attempts (incl. relaxation)">{insp.retrieval_attempts}</Row>
                <Row k="Embedder">{insp.embedder}</Row>
                <Row k="Answer written by">{insp.generator_used === "llm" ? "Gemini (grounded)" : "Template"}</Row>
                <Row k="Injection flags">{insp.injection_flags.length ? insp.injection_flags.join(", ") : "none"}</Row>
              </dl>
              {chat.results.length > 0 && (
                <ol className="mt-2 list-decimal pl-5 text-xs">
                  {chat.results.map((r) => (
                    <li key={r.item_id} className="num">
                      {r.food_item}: cosine {r.cosine_distance?.toFixed(3)}, {r.distance_km.toFixed(2)} km
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <Timings timings={insp.timings_ms} />
          </>
        ) : (
          <p className="text-sm text-slate-mute">Run a search to see how it was parsed, filtered and ranked.</p>
        )}
      </div>
    </aside>
  );
}
