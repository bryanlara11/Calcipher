# AGENTS.md — CalCipher Project Context & Architectural Contract

> **AI Agent Instruction:** Read this file at the start of every session. Treat the architecture,
> invariants, and conventions below as strict ground truth. Update the
> `Current Implementation Status` section at the end of every completed task.

## 1. Project Overview & Core Data Reality

**CalCipher** is a localized Hybrid Retrieval-Augmented Generation (RAG) web application that
solves nutritional opacity when eating out in the Philippines.

**CRITICAL DATA ARCHITECTURE NOTE**

- Nutrition data is scraped from **`@anongcaloriesnito`** on Instagram with `instaloader` +
  `pytesseract` OCR. Posts contain **brand/chain names, food item names, serving sizes, calories
  and macros**, but **NO geographic coordinates or branch addresses**.
- Physical locations (`latitude`, `longitude`) come dynamically from the free **OpenStreetMap
  Overpass API** (`nwr["amenity"~"restaurant|fast_food|cafe"](around:radius,lat,lng); out center;`)
  and are rendered on a **React-Leaflet** map with OSM tiles. No paid map keys anywhere.
- The two datasets are linked by the **Brand-to-Branch Entity Resolution & Spatial Join**
  pipeline: OSM POI names/brand tags (e.g. `"Andok's - Pedro Gil"`) are normalized and matched to
  `nutrition_catalog.brand_slug` (e.g. `"andoks"`).

## 2. Repository Structure

```text
calcipher/
├── AGENTS.md                        # This file (CLAUDE.md just imports it)
├── docker-compose.yml               # PostgreSQL 16 (pgvector + pg_trgm) + backend
├── docker/initdb/01-extensions.sql  # CREATE EXTENSION vector, pg_trgm
├── backend/
│   ├── Dockerfile                   # python:3.12-slim + tesseract-ocr
│   ├── requirements.txt             # runtime deps (incl. sentence-transformers, instaloader)
│   ├── requirements-dev.txt         # pytest, pytest-asyncio, pgserver (embedded PG for tests)
│   ├── pytest.ini
│   ├── run.py                       # Dev launcher (sets Windows selector loop for psycopg async)
│   ├── dev_db.py                    # Starts embedded PG16 + pgvector (no Docker) and prints its URL
│   ├── .env.example
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory, CORS, rate limit, request IDs, routes
│   │   ├── ingest.py                # Staged ingestion CLI: scrape → ocr → parse → (review) → load, reembed, status
│   │   ├── config.py                # Pydantic Settings env validation
│   │   ├── models.py                # nutrition_catalog, osm_branch_cache, overpass_fetch_log
│   │   ├── schemas.py               # Pydantic v2 request/response & SearchConstraints
│   │   ├── security.py              # Sanitization, <user_query> sandbox, macro physics, rate limiter
│   │   ├── services/
│   │   │   ├── embedder.py          # Singleton RoBERTa embedder (768-dim) + hashing test backend
│   │   │   ├── overpass_client.py   # Overpass fetcher (Semaphore(1), mirrors) + 24h grid DB cache
│   │   │   ├── brand_matcher.py     # Normalize & match OSM names -> brand_slug (prefix/alias/trigram)
│   │   │   ├── llm.py               # Gemini (google-genai) wrapper; optional
│   │   │   ├── query_parser.py      # LLM + deterministic regex constraint extractor
│   │   │   ├── generator.py         # Grounded answer generation + template fallback + grounding check
│   │   │   ├── hybrid_rag.py        # Spatial join + hard SQL filters + pgvector ranking + relaxation
│   │   │   └── ingestion_ocr.py     # OCR building blocks: text parser, OpenCV preprocessing, Tesseract, upsert
│   │   └── db/
│   │       ├── session.py           # Async engine/session, init_db (extensions, tables, indexes)
│   │       ├── maintenance.py       # reembed_catalog, count_stale_embeddings, rematch_branches
│   │       └── seed.py              # Demo catalog + branches (toggleable), --purge-demo-items, --reembed
│   └── tests/                       # pytest suite (unit + embedded-Postgres integration)
└── frontend/
    ├── package.json, vite.config.js, tailwind.config.js, postcss.config.js, index.html
    └── src/
        ├── main.jsx, index.css
        ├── App.jsx                  # Split-pane Map + RAG Chat + Inspector UI
        ├── components/
        │   ├── CalorieMap.jsx       # React-Leaflet map: matched (colored) vs unmatched (grey) pins
        │   ├── ChatPanel.jsx        # Conversational UI & quick-filter chips
        │   └── RagInspector.jsx     # Debug drawer: Overpass source, parser, SQL candidates, timings
        └── api/client.js            # Fetch wrapper for FastAPI endpoints
```

## 3. Database Contract

### `nutrition_catalog` (location-agnostic, scraped)
`id UUID PK`, `brand_name`, `brand_slug` (indexed), `food_item`, `calories INT` (indexed,
`CHECK 0..10000`), `protein_g/carbs_g/fat_g NUMERIC(6,2)` nullable (`>= 0`), `serving_size`,
`is_verified BOOL default false`, `source_post_url`, `context_string` (canonical text that is
embedded), `embedding VECTOR(768)` with HNSW `vector_cosine_ops`, `embedding_model` (which
embedder produced the vector; added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `init_db`),
`created_at`.
Unique: `(brand_slug, food_item, serving_size)` → idempotent seeding/ingestion.

### `osm_branch_cache` (spatial layer)
PK **`(osm_type, osm_id)`** — *deliberate deviation from a bare `osm_id` PK*: OSM node, way and
relation IDs are separate number spaces and collide. `osm_type ∈ {node, way, relation, seed}`;
`seed` rows are approximate offline-demo branches and are purged when a live Overpass fetch
covering their location succeeds. Other columns: `poi_name`, `matched_brand_slug` (indexed),
`amenity_type`, `latitude` (`CHECK 4.5..21.5`), `longitude` (`CHECK 116..127`), `address_hint`,
`last_fetched_at TIMESTAMPTZ`. GIN `gin_trgm_ops` index on `poi_name` when pg_trgm exists.

### `overpass_fetch_log` (spatial grid cache bookkeeping — additive table)
Records every successful Overpass fetch (`grid_key`, snapped center, `radius_m`, `element_count`,
`fetched_at`). A request is a **cache hit** when a log row younger than 24h has a circle that fully
contains the requested circle (`dist(center, point) + r_req <= r_logged`). This is needed because
an empty neighborhood would otherwise always look like a cache miss.

## 4. Four-Stage Pipeline (`/api/v1/chat`, `/api/v1/map-items`)

1. **Discovery** (`overpass_client.BranchDiscoveryService`): snap to 0.01° grid, check fetch log,
   else query Overpass (POST, `User-Agent: CalCipher-UPM-Academic-Project/1.0`, mirror fallback on
   429/5xx/timeouts, `remark` runtime errors treated as failure), match names → slug, upsert.
   On failure → `stale_cache_fallback` (whatever is in `osm_branch_cache` is used).
2. **Intent extraction** (`query_parser.QueryParser`): Gemini JSON extraction when a key is set,
   always cross-checked by the deterministic regex parser (regex numbers win; LLM brands must
   resolve to a known slug). Regex-only when the LLM is offline.
3. **Hybrid retrieval** (`hybrid_rag.RETRIEVAL_SQL`): one constant, fully parameterized SQL:
   bbox + Haversine CTE over branches → JOIN catalog on slug → hard filters (calories, protein,
   brand, distance) → `DISTINCT ON (item)` nearest branch → ORDER BY `embedding <=> :query_vec`
   → top K=5, plus `COUNT(*) OVER ()` candidate count.
4. **Grounded generation** (`generator.GroundedGenerator`): only the K records go to the LLM inside
   `<retrieved_records>`; user text inside `<user_query>`. LLM output is rejected (template used)
   if it cites calorie numbers not present in the records. **Relaxation ladder** on zero results:
   calories +15% → distance 5 km → both; the notice is prepended deterministically.

## 5. Invariants (do not break)

- **Never interpolate user input into SQL.** All SQL in `hybrid_rag.py` is module-level constant
  text with bound params (`:user_lat`, `:user_lng`, `:max_cal`, `:query_vec`, …).
- **Macro physics before I/O:** `min_protein_g * 4 > max_calories` → HTTP 422 before Overpass/DB.
- **One embedding space:** the catalog and queries must be embedded by the same backend. Every
  write stores `embedding_model`; rows that differ from the running embedder are "stale" (startup
  warning + `stale_embeddings` in `/api/v1/health`). Fix with `python -m app.ingest reembed`.
  Default model `sentence-transformers/all-distilroberta-v1` (RoBERTa, 768-dim).
  Note: `all-roberta-large-v1` is **1024-dim** and is incompatible with `VECTOR(768)`.
- **Context strings** are always produced by `ingestion_ocr.build_context_string` (seed + OCR).
- **Ingestion is staged** (`app/ingest.py`): scrape → `data/raw`, ocr → `data/ocr` (cached by
  image sha256 + engine id; bump `PREPROCESS_VERSION` when preprocessing changes), parse →
  `data/review/parsed.csv` + appends *new posts only* to `reviewed.csv` (human-owned, never
  rewritten), load → validates all-or-nothing, upserts, then `rematch_branches`.
- **Verification is human-only:** rows become `is_verified` only via `verified=yes` in
  reviewed.csv. Unverified writes never overwrite verified rows; demo seeding never overwrites
  scraped (`source_post_url` set) or verified rows; `--purge-demo-items` never deletes them.
- **Branch matches are refreshed after brand changes:** `rematch_branches` only fills unmatched
  branches or clears slugs whose brand left the catalog (the OSM brand tag isn't cached). The API's
  `BrandRegistry` reloads brands every 5 minutes.
- **Overpass fair use:** descriptive User-Agent, `asyncio.Semaphore(1)`, 24h grid cache,
  single-flight lock around check→fetch→store, radius capped at `OVERPASS_MAX_RADIUS_M` (5 km).
- Seed nutrition values are **illustrative estimates for offline development**, not scraped data;
  seed branch coordinates are **approximate** (`osm_type='seed'`).

## 6. Conventions

- Python 3.11+, SQLAlchemy 2.0 async + psycopg3 (`postgresql+psycopg://`). On Windows, psycopg
  async needs the selector event loop → run the API via `python run.py` (sets the policy).
- No Alembic yet: `init_db()` runs `create_all` + extension/index DDL idempotently.
- Tests: `cd backend && .venv/Scripts/python -m pytest` (Windows) / `python -m pytest`.
  DB tests use `TEST_DATABASE_URL` if set, else an embedded `pgserver` (PG16 + pgvector, **no
  pg_trgm**), else they skip. Unit tests never touch the network or download models
  (`EMBEDDER_BACKEND=hash`).
- Frontend: Vite dev server on :5173 proxies `/api` → `http://localhost:8000`.

## 7. Current Implementation Status

_(updated at the end of every completed task)_

**2026-11-17 — initial build complete**

- [x] Schema: `nutrition_catalog`, `osm_branch_cache` (PK `(osm_type, osm_id)`), `overpass_fetch_log`;
      HNSW cosine index; conditional pg_trgm GIN index.
- [x] Seed: 27 illustrative catalog items across 9 chains; 25 approximate branches in Taft/UP
      Manila, Makati, QC (incl. 2 unmatched brands for grey pins). Idempotent; never re-adds seed
      pins inside areas that already received live Overpass data.
- [x] Overpass client: POST + UA, `Semaphore(1)`, mirror fallback (429/5xx/timeouts), `remark`
      errors rejected, dedupe; discovery service with 0.01° grid + 24 h coverage log, single-flight
      lock, seed purge, and a **failure circuit breaker** (`OVERPASS_FAILURE_COOLDOWN_S`, default
      300 s) so an Overpass outage doesn't add ~40 s to every request.
- [x] Brand matcher (prefix → containment → pg_trgm-compatible trigram), alias table.
- [x] Query parser (regex always; Gemini merged when `GEMINI_API_KEY` set; explicit regex numbers
      win, heuristic "high protein" = 20 g yields to an explicit LLM value).
- [x] Hybrid retrieval SQL + relaxation ladder + grounded generator with calorie grounding check.
- [x] Security: sanitization/flags, `<user_query>` sandbox, macro physics 422 before I/O,
      sliding-window rate limit (single-process), request IDs, no stack traces in 500s.
- [x] OCR pipeline: instaloader download, OpenCV preprocessing, Tesseract, parser with OCR digit
      repair, Atwater check (`is_suspect`), upsert that never overwrites verified rows, CLI.
- [x] Tests: 171 (170 pass, 1 skipped: real-Tesseract roundtrip needs the binary). DB tests ran
      against embedded PG16 + pgvector via `pgserver`.
- [x] Frontend: React 18 + react-leaflet 4 + Tailwind 3; map with matched/unmatched toggle, radius,
      draggable pin, brand menus in popups; chat with nutrition-facts result panels; inspector drawer.
- [x] Dev tooling: `backend/dev_db.py` (embedded Postgres), `backend/run.py`, `.claude/launch.json`.

**2026-11-17 — ingestion split + data-loading gaps fixed**

- [x] `python -m app.ingest scrape|ocr|parse|load|reembed|status` with per-stage files under
      `backend/data/` (git-ignored). Scrape writes meta last so interrupted posts retry; stops on
      rate-limit/login errors. OCR cached per image. Parse learns brands from reviewed.csv and adds
      placeholder rows for posts with no parsed items.
- [x] Tesseract auto-detected (PATH, then `C:\Program Files\Tesseract-OCR`); `TESSERACT_CMD` and
      `OCR_LANG` settings. Real-Tesseract roundtrip test now runs (Tesseract 5.5.3).
- [x] Parser joins wrapped item names ("Chicken Inasal Pecho" / "without Rice").
- [x] `SEED_DEMO_ITEMS` / `SEED_DEMO_BRANCHES` toggles, `--purge-demo-items`.
- [x] `embedding_model` tracking, `reembed` over all rows, stale count in health + startup warning.
- [x] Tests: 196 passing, none skipped.
- Not yet run against real Instagram posts (scrape needs your login session).

**Not yet verified / known gaps**

- Live Overpass fetch was **not** exercised end-to-end: Overpass was unreachable from the dev
  machine (timeouts on both mirrors). The path is covered by mocked-httpx + DB tests only.
- The RoBERTa backend (`sentence-transformers`, torch) and Gemini were not run; local verification
  used `EMBEDDER_BACKEND=hash` and the template generator.
- docker-compose / Dockerfile were written but not built (Docker isn't installed on the dev machine).
- OCR parsing is tuned to plausible formats; it has not been run against real
  @anongcaloriesnito posts yet. Pilot ~15 posts, inspect `data/ocr/*.json` + reviewed.csv, then
  adjust `_CAL_PATTERNS`/`_MACRO_PATTERNS` and add real samples as test cases.
- No Alembic migrations; rate limiter is per-process (use Redis for multiple workers).

**Suggested next steps**: pilot the ingestion CLI on real posts, then switch to the RoBERTa
embedder + `python -m app.ingest reembed`, set `SEED_DEMO_ITEMS=false` + `--purge-demo-items`,
verify live Overpass from a normal network, add Alembic.
