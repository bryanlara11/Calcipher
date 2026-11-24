# AGENTS.md — CalCipher Project Context & Architectural Contract

> **AI Agent Instruction:** Read this file at the start of every session. Treat the architecture,
> invariants, and conventions below as strict ground truth. Update the
> `Current Implementation Status` section at the end of every completed task.

## 1. Project Overview & Core Data Reality

**CalCipher** is a localized Hybrid Retrieval-Augmented Generation (RAG) web application that
solves nutritional opacity when eating out in the Philippines.

**CRITICAL DATA ARCHITECTURE NOTE**

- Nutrition data comes from authorized local originals/captions (recommended) or authorized
  **`@anongcaloriesnito`** Instaloader access, followed by `pytesseract` OCR. Posts contain **brand/chain names, food item names, serving sizes, calories
  and macros**, but **NO geographic coordinates or branch addresses**.
- The user selected no-key OpenStreetMap/Overpass mode on 2026-11-24. Leaflet displays OSM
  locations; the existing spatial cache joins them to nutrition by matched brand slug. Google
  Maps/Places support remains available when both browser and server keys are configured.
  Google listings join through a request-scoped SQL recordset and display only on Google Maps.
  `/api/v1/map-config` exposes capability flags, never server secrets; map/chat requests select
  the matching service through `X-CalCipher-Map-Provider`. Do not mix provider locations on maps.

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
│   │   │   ├── hybrid_rag.py        # Approved-only spatial join, exact hard filters, pgvector ranking
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
        ├── App.jsx                  # Search / Nearby / Chat / Settings hash pages; shared state
        ├── components/
        │   ├── CalorieMap.jsx       # Lazy Leaflet/OSM or Google map, selected with discovery provider
        │   ├── ChatPanel.jsx        # Conversational UI & quick-filter chips
        │   └── RagInspector.jsx     # Debug drawer: actual provider/source, parser, SQL candidates, timings
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
Additive optional fields: `price_php NUMERIC(10,2)`, `price_source_url TEXT`. A price requires
nonnegative value, source and human verification. Missing prices remain null; unknown servings
are SQL NULL. `provenance JSONB NOT NULL DEFAULT '{}'` preserves classification, collection date,
per-field source links, snapshot hashes and the actual human approval reference. The unique
identity uses PostgreSQL NULLS NOT DISTINCT to make repeated unknown-serving imports idempotent.

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
   `<retrieved_records>`; user text inside `<user_query>`. The LLM may return only a retrieved item ID;
   all factual text is rendered by code. Unknown IDs/extra claims use the template. **Exact hard limits:**
   calorie, protein, budget and distance constraints never widen automatically on zero results.

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
- **Ingestion is staged** (`app/ingest.py`): authorized import-local (or scrape) → `data/raw`, ocr → `data/ocr` (cached by
  image sha256 + engine id; bump `PREPROCESS_VERSION` when preprocessing changes), parse →
  `data/review/parsed.csv` + appends *new complete posts only* to `reviewed.csv` (human-owned, never
  rewritten). New rows start skip/no. Load validates all-or-nothing and commits catalog upserts
  and `rematch_branches` together. Parse slides independently and reject stale image/preprocessing OCR.
- **No invented availability:** missing macros/prices are null, missing servings are explicitly
  unavailable, and missing calories remain skipped review placeholders. Prices require checked
  source evidence. `max_price_php` is a bound SQL filter that excludes null prices and never relaxes.
- **Verification is human-only:** rows become `is_verified` only through an actual human review
  action reflected in reviewed CSV or a dataset-hash-bound approval receipt. Pending records are
  excluded from retrieval, map nutrition counts, menus and the brand matcher. Existing verified
  corrections require explicit `load --allow-reviewed-updates` to change. Unverified writes never overwrite verified rows; demo seeding never overwrites
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

**2026-11-25 — authorized nutrition pilot and strict approval gate**

- Preserved navigation, no-key maps, natural tiles and black/muted-sage appearance.
- One bounded Python Instagram attempt failed at post metadata. Public browser fallback retrieved
  one full eight-slide 7-Eleven carousel/caption; a second Army Navy post was partially inspected.
- Collected 36 source-backed candidates across six PH brands, compared visible HTML/JSON-LD,
  and checked six KFC rows against the original official table. One live request also verified
  the new bounded secondary-source collector. None of this proves automatic Instagram access.
- Loaded 36 real pending records into the persistent PostgreSQL catalog: 36 calorie, nine protein,
  six carbohydrate, six fat values; zero current PHP prices; zero approved records while specific
  human-approved IDs are outstanding. Production excludes all pending records.
- Added provenance, nullable servings with NULL-safe uniqueness, immutable review/approval receipts,
  protected reviewed updates, bounded secondary collection and assisted image-region observations.
- User requires exact calorie/distance limits; this supersedes historical relaxation behavior.
- Real OCR: eight images processed; seven unsafe full-image proposals all skipped; assisted crops
  recovered a calorie candidate in one of three regions. Human-verified accuracy remains pending.
- 302 backend tests and 30 frontend tests pass; lint and production build pass. These tests use
  fixtures for safeguards and do not establish successful live approved recommendations.
- GitHub account dpsahagon was verified. Terminal push published this feature branch successfully.
  Connector blob and draft-PR writes return HTTP 403, `Resource not accessible by integration`;
  no PR was opened. Shared main remains unchanged.
- See docs/DATA-COLLECTION.md for commands and evidence boundaries. Approved-ID promotion and
  real approved recommendation demonstration remain dependent on the operator's selected IDs.



**2026-11-25 - natural map colors and muted sage accents**

- Removed the OSM green filter and Google theme recoloring. Both map providers now retain their
  natural colors in either appearance mode; the radius overlay uses a subtle neutral blue-grey.
- Replaced bright dark-mode greens with muted sage: body #AEC1AD, accents #92AD94 and buttons
  #8FA88F. Black backgrounds remain. This supersedes the prior bright green/map tint choice.
- 27 frontend tests, including both themes' contrast/axe checks, ESLint and build pass.
- Browser verified natural tiles (computed filter: none), the sage palette in Dark mode and
  264 real restaurant locations. Restored the user's observed Light preference after checking.
  No backend/discovery behavior changed.



**2026-11-24 - black/green theme, logo-only navigation and live discovery repair**

- Removed navigation numbers, repeated CalCipher/Meals & nutrition text, breadcrumb brand text
  and the bottom rail icon. The original top logo and useful source attribution remain.
- Dark mode uses black (#000000) with green text (#9AE1AF), near-black cards, green accents and
  a matching darkened OSM basemap. Browser theme-color follows the selected appearance.
- Settings is now a dedicated component with appearance, locations and nutrition/price cards;
  verification/privacy and optional Google setup stay collapsed. An API reconnect action clears
  stale connection failures after a backend restart. Current map health overrides older chat errors.
- Replaced the failing default discovery path with the OSM-listed Asia regional endpoint
  `https://ovp.dresel.systems/api-as/interpreter`, retaining global Overpass/Private.coffee fallbacks,
  caching, single-flight requests and cooldowns. API restarted with the new endpoint.
- Live API: 264 restaurants within 1 km of UP Manila; first fetch stored 660 named OSM locations.
  Widened chat discovery also succeeded (3654 named POIs fetched), with no discovery errors.
  These are real OSM locations. Nutrition catalog remains empty; no meal facts/prices were invented.
- Repeated per-message outage paragraphs removed; failures have one short connection status.
  Map/list resizing ignores hidden map dimensions and locate actions return to the top of the map.
- 284 backend and 27 frontend tests pass; ESLint/build and diff check pass. Added meaningful
  regression checks for API reconnect and a single warning across repeated failed chat searches.
  Both themes/all four pages retain axe structural and CSS token contrast checks.
- Browser verified desktop/mobile Settings, actual black/green colors, absence of rail numbers and
  bottom icons, live map/list with real pins, and live chat with no discovery warning. Normal
  viewport restored; Dark mode selected; local tab left on Settings for review. Main unchanged.



**2026-11-24 - no-key maps and focused navigation**

- User chose OpenStreetMap without keys, with Google retained. Added Leaflet 1.9.4; basemap
  loads without configuration. Restored the existing Overpass cache/discovery service as the
  no-key provider; updated its old Kumi mirror to Private.coffee. No invented keys/data.
- Capability endpoint and explicit request provider keep Google Places data on Google Maps.
  Both Google keys must be present for the UI to select Google; server secrets never leave API.
  Google script errors/auth failures/timeouts are visible. Existing quota cooldown remains.
- Separate Search, Nearby, Chat and Settings hash pages; desktop rail/mobile bottom navigation.
  Nutrition/area filters open on demand. Search form and transcript persist across navigation;
  stale searches abort after location/radius changes. Map/list, pan-to-search, geolocation,
  menus, source/verification information, inspector and Light/Dark/System are retained.
- System fonts and 16px body/input text retained; quieter cream/forest design with the supplied
  logo and direct wording. Map code is loaded only on Nearby; initial radius is 1 km.
- 284 backend tests and 25 frontend tests pass, including provider isolation/secrets, OSM ID
  namespaces, route/state behavior, panning, cancellation and all four pages' structural axe
  checks in both themes. ESLint/build pass; npm full audit reports zero vulnerabilities.
- Browser: live OSM tiles, map keyboard panning, Search this area, Back/Forward, direct refresh,
  live chat/inspector, appearance and desktop/768px/390px layouts verified. No horizontal
  overflow in inspected views. Vite/API restarted and left running locally.
- External limitation: public Overpass attempts returned 502/timeouts (alternate public service
  also returned 504); listings show an explicit outage with cached fallback. Basemap remains
  usable. Real catalog is still empty; Google mode cannot be live-tested without credentials.
  No production nutrition or demo data was added. Main remains unchanged.


**2026-11-24 - simpler wording and readable typography**

- Replaced marketing slogans and decorative italic headings with direct search/map wording.
- Uses the system sans-serif font, 16px body/input text and larger supporting labels in both themes.
  Removed external font loading and the decorative map grid; retained the supplied logo and all controls.
- Frontend lint, 14 tests and production build pass. Desktop and 390px mobile views checked;
  mobile input text is 16px with no horizontal overflow. Backend/API behavior is unchanged.


**2026-11-24 - branded frontend, security fixes and ingestion verification**

- Retained every prior ingestion improvement and the existing React/FastAPI/Google/pgvector architecture.
- Supplied logo optimized as WebP; simplified editable cloche/chart SVG icon; cream/forest design
  tokens, responsive meal search/map/chat/inspector, persistent Light/Dark/System and early theme init.
- Vite 6.4.6, PostCSS 8.5.33, patched Vitest 4.1.12; React/Tailwind unchanged. Node 22.23.5 with
  npm 11.21.3 used for checks; no global Node relinking. Full/prod npm audits: zero findings.
- 278 backend tests (original 273 preserved), 14 frontend tests, ESLint and production build pass.
  Browser desktop/tablet/mobile and both themes checked. Full evidence: docs/AUDIT.md.
- Inline PHP/₱ price proposals remain skip/no until checked; source_images CSV -> JSONB -> API
  traces contributing slides. Existing verified corrections/provenance remain protected.
- Real public US restaurant PDF OCR was attempted; full-table parsing produced zero items.
  Assisted verification proposals remained unverified in an isolated database, never PH catalog data.
- Original live Instagram retrieval remains unresolved. Authorized owner media/export and human
  review are the fallback; Google credentials/billing are needed for live maps. Gemini/Docker unverified.
- GitHub connector verifies dpsahagon/profile email and repository push access. User authorized
  commit/publication/draft PR on this branch after testing; no force-push or main merge.
  Actual outcome: local commit under dpsahagon@up.edu.ph; connector writes fail HTTP 403 and
  terminal Git/SSH credentials are absent. No branch publication or PR; commands in OPERATIONS.md.


**2026-11-23 — repository audit and authorized ingestion stabilization**

- [x] Public repository cloned into the selected empty workspace; `origin` remains
      bryanlara11/Calcipher. Work is local on `dpsahagon/instagram-ingestion-fix`, with no commits,
      pushes or PR creation pending the user's explicit approval. GitHub authentication as
      dpsahagon is unavailable; inherited email needs confirmation before authorship configuration.
- [x] Authorized local manifest import with ordered carousels, captions/provenance, preflight
      validation, identical-import skipping and conflicting-import preservation. No live access required.
- [x] Session failures are recoverable; session identity and download image completeness are
      checked. 401/429/auth refusals stop collection. No access-control/rate-limit evasion.
- [x] Atomic/fresh OCR caches; EXIF/bounded preprocessing; PSM 3/6/11; independent slide parsing;
      conflict/ambiguity flags; explicit missing serving; zero/thousands calorie support; exact
      decimal macro context; all generated review rows require explicit action=load.
- [x] Transactional catalog load/rematch; existing reviewed edits/verified row protections retained.
      Nullable source-backed PHP prices, budget SQL filtering, availability/verification UI and
      record-ID-only LLM selection prevent unsupported numerical prose.
- [x] Baseline 216 backend tests passed. Updated suite: 273 passed, no failures/skips. Real
      Tesseract and embedded PostgreSQL/pgvector exercised with synthetic sources; network/LLM
      responses mocked. Frontend production build and pip check pass. Production RoBERTa loads
      and produces 768 dimensions; local API/Vite start and health is OK.
- [x] README, docs/OPERATIONS.md, docs/AUDIT.md, manifest example, tested Python constraints,
      and draft PR text provide reproduction and GitHub handoff commands.
- Still external/unverified: authorized real source media, live Instagram, Google keys/billing,
      Gemini, Docker. The persistent audit dev catalog is empty; tests use synthetic fixtures.
      Existing npm audit: esbuild moderate, postcss high, vite high; separate security update needed.
      Historical status entries below are retained as history, not newly reproduced live evidence.

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

**2026-11-17 — Instagram scrape interruption handling**

- The scraper stops on a 401 or 429 during post listing and reports how many posts finished. Completed posts are skipped on a later run. A regression test covers Instagram refusing the initial post listing.

**2026-11-17 — first real menu-slide OCR pilot**

- Ran OCR and parse on the menu portion of a user-supplied screenshot of 24 Chicken, slide 2. Tesseract recovered some flavor names but missed the small per-serving numbers. Parse produced zero items and a skipped placeholder. A higher-resolution source is still needed to assess calorie extraction.

**2026-11-17 — McDonald's carousel OCR pilot**

- Four manually cropped kiosk labels produced zero parsed items. OCR read the chicken and sauce calorie lines correctly, recovered the Ube dessert number with a corrupted unit, and missed the Choco Pinipig value. A separate unverified transcription is available under backend/data/pilot-mcdonalds/review/transcribed.csv for human review.

**2026-11-17 — McDonald's kiosk parsing**

- Added McDonald's to the OCR brand list and handled PHP prices and wrapped kiosk item names. Re-parsing the cached pilot recovered the chicken (526 kcal) and sauce (32 kcal). The two dessert labels still need human review.

**2026-11-17 — Google Maps restaurant discovery**

- Map and chat use live Places listings, with no branch seeding requirement. A server Places key and a restricted Maps JavaScript browser key are needed for live verification.
- Verified with mocked Places responses, embedded PostgreSQL and the frontend production build. All 202 backend tests passed.
- Live pilot verified with a Maps Demo Key. The browser displayed Google restaurant markers, and the under-600-kcal chat search returned three catalog items for the Google listing at UN–Taft.

**2026-11-17 — Restaurant-only map**

- Nearby and brand searches filter restaurant types. The Google base map hides place and station labels, and restaurant markers use larger circles with numbered search results.

**2026-11-17 — Five-review minimum**

- Map and brand searches request Google Places userRatingCount and exclude restaurants with fewer than five ratings or no reported count before brand matching. All four provider tests passed. The live UN Avenue map returned eight qualifying listings.

**2026-11-17 — Radius coverage in dense areas**

- Saturated map searches sample the visible portion of the selected radius, merging Google Place IDs and enforcing distance from the original pin. Live 5 km verification from UP Manila returned 66 restaurants including the UN–Taft McDonald’s and Popeyes.

**2026-11-17 — Google Places quota handling**

- Each map search now requests a single result set for the visible area. Panning and zooming expose Search this area. The API shares a Places 429 cooldown between map and chat and returns Retry-After. This supersedes the nine-cell map expansion. The frontend built and 29 provider/retrieval tests passed. Google still returned 429 during live verification.

**2026-11-17 — Radius slider**

- The map starts at 100 m and supports 100 m increments through 5 km. Dragging previews the radius and releasing applies the selection to discovery and chat.

**2026-11-17 — Mobile map-first layout**

- Chat starts hidden behind a Chat button at the top right of the map; its panel and the retrieval inspector each have a close button, and the inspector can be reopened from the chat header. While chat is closed the map fills the screen with only the controls bar and legend. Map error notices can be dismissed and reappear on the next failure. Google's fullscreen control is off so it doesn't sit under the Chat button. Checked at 375×812 and desktop width; the frontend builds.

**2026-11-17 — Opening splash**

- `SplashScreen.jsx` shows an original two-tone flame mark (calorie "burn", not a copy of any existing character), "CalCipher" and "Decipher your food." on the ink background: content fades in, holds, then the screen fades out (~2.1 s). Shown once per browser tab via sessionStorage; tap to skip; a 2.6 s timer clears it if animations never run (background tab, animations disabled). Checked at desktop and 375×812; the frontend builds.

**2026-11-17 — dev_db.py keeps .env in sync**

- `python dev_db.py` now writes the embedded database's URL into `backend/.env` on every run (creating the file if missing), since the server can return on a new port after a restart. It only replaces a `DATABASE_URL` shaped like its own (`postgres@127.0.0.1|localhost:<port>/calcipher`); any other database URL is left untouched. `--no-env` prints only. The write is atomic and keeps other lines and line endings. Covered by `tests/test_dev_db.py`.

**2026-11-17 — Places quota protection**

- `GooglePlacesDiscovery` reuses responses for the same search for `PLACES_CACHE_TTL_S` (default 300 s; pin rounded to 4 decimals, results still distance-filtered from the exact pin), merges concurrent identical requests into one call, and fails fast during a cooldown. 429 without Retry-After backs off 60 s, doubling per consecutive 429 up to 30 min, reset on success; other HTTP errors pause 30 s. The cooldown persists in `backend/data/places_cooldown.json` (`PLACES_STATE_PATH`) so an API restart does not re-hit Google. The API sends Retry-After on 429/503; the frontend skips map requests until it passes and disables Retry meanwhile, with no automatic refetch when it ends. Tests count real calls (`tests/test_google_places.py`). Live check: six map requests during a quota block produced one Google call. Note: Google's Places terms restrict caching most content; keep the TTL short or set it to 0.
