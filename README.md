# CalCipher

Hybrid RAG for Philippine restaurant calories: nutrition data from **@anongcaloriesnito** (OCR) is
joined to real restaurant locations from **OpenStreetMap/Overpass** or **Google Places API (New)**, filtered by hard
SQL constraints (calories, protein, distance), and ranked with **pgvector**.

Architecture, invariants and status: see [AGENTS.md](AGENTS.md).

The ingestion path now supports **authorized local carousel images and captions** without
Instagram access, human review before loading, nullable source-backed PHP prices, and hard
budget filters. The original FastAPI/React/Google Places/pgvector architecture and 768-dimensional
RoBERTa model are retained. The LLM can select a retrieved record; factual text comes from storage.

- [Operator guide: macOS setup, import, review, database, demo and troubleshooting](docs/OPERATIONS.md)
- [Audit: findings, evidence, tests, limitations and GitHub handoff](docs/AUDIT.md)

## Branded interface and current verification

Light, Dark and System modes persist locally. The supplied logo, simplified SVG icon, responsive
Search, Nearby, Chat and Settings pages use cream/forest in Light mode and black with muted sage-green text in Dark mode. Maps keep their natural colors. The sidebar keeps the supplied logo without repeated brand text or navigation numbers. Settings uses separate appearance, locations and data cards, with technical setup collapsed. Nutrition and map filters open on demand; mobile uses bottom navigation. Search fields and chat history stay in memory across page changes. Inline PHP prices and slide
references are review proposals; live Instagram retrieval is still unverified, not fixed by import.
Current checks: 284 backend tests, 27 frontend tests, lint/build pass; npm audits report zero findings.
Use Node 22 LTS and npm 11.21.3 for the tested frontend setup (`npx --yes npm@11.21.3 ci`).

## Quick start on macOS

From the repository root (Python 3.12; Node 22 LTS recommended):

```bash
brew install tesseract node@22
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c requirements-tested.txt
.venv/bin/python dev_db.py
SEED_DEMO_ITEMS=false SEED_DEMO_BRANCHES=false .venv/bin/python -m app.db.seed
.venv/bin/python run.py
```

The tested Python dependency snapshot is in `backend/requirements-tested.txt`. `dev_db.py`
creates `.env` with its database URL; preserve that URL and set `SEED_DEMO_ITEMS=false` there for
a real-data pilot. Do not overwrite an existing `.env`. Start the frontend in another terminal
with `cd frontend`, `npm ci`, then `npm run dev -- --host 127.0.0.1`. The map works without keys using Leaflet/OpenStreetMap tiles and cached Overpass discovery. Public services can time out; the UI reports discovery outages instead of treating them as empty results. Google remains optional when both keys are configured.

## Recommended local ingestion

Ask the owner for originals, captions and permission or an authorized export. Prepare
`backend/source-data/posts.json` using the [manifest format](docs/OPERATIONS.md#import-authorized-local-media).
From `backend/`:

```bash
.venv/bin/python -m app.ingest import-local --manifest source-data/posts.json
.venv/bin/python -m app.ingest ocr --lang eng --psm 6
.venv/bin/python -m app.ingest parse
```

New review rows start `action=skip, verified=no`. Check and correct `data/review/reviewed.csv`
against the originals, opt in with `action=load`, and set `verified=yes` only after human checking.
Then run `.venv/bin/python -m app.ingest load --dry-run`, followed by `load`. Reruns preserve
review edits. Missing macros/prices remain null and missing servings show `serving size unavailable`.
Prices require a source and human verification; budget searches exclude unknown prices.

## Map providers and Google setup

The frontend checks `GET /api/v1/map-config?browser_key_configured=true|false`. With both keys it
uses Google Maps + Places; otherwise it uses OpenStreetMap + Overpass. Map/chat requests send
`X-CalCipher-Map-Provider` so Google listings are never placed on an OSM map. The server key is
never returned to the browser. External map code loads only on the Nearby page. OSM attribution
remains visible; tiles are fetched normally from `tile.openstreetmap.org` without prefetching.

The initial search radius is 1 km; expand it in **Nearby → Area & filters**. Pan and choose
**Search this area** to move the search center. **List** shows the same discovered restaurants.
Use **Settings** for appearance, data notes and Google configuration status.

Overpass discovery keeps the existing 24-hour spatial cache, one-request semaphore, mirror fallback
and failure cooldown. Philippines searches try the [Asia regional endpoint listed by OpenStreetMap](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances)
first, followed by the global Overpass and Private.coffee endpoints. The regional endpoint was
live-tested on 2026-11-24: 264 real restaurant locations within 1 km of UP Manila, plus a successful
5 km widened chat discovery. Nutrition remains unavailable until the source catalog is loaded.
Public services have no availability guarantee. For a production launch, use a provider with capacity
appropriate to your traffic. The nutrition catalog remains separate: location pins alone supply no
nutrition facts or prices.


Enable billing, **Places API (New)** and **Maps JavaScript API** in your Google Cloud project.
Set a Places-restricted server key in backend/.env as GOOGLE_PLACES_API_KEY.
Set a separate Maps JavaScript key in frontend/.env as VITE_GOOGLE_MAPS_API_KEY.
Restrict the browser key to your website referrers (for local Vite, http://localhost:5173/*).
Restart the API and Vite after editing their environment files.

In Google mode, the map keeps restaurants with at least five Google ratings within the pin radius. Each map search uses one Google Nearby Search (up to 20 listings before filtering). After panning or zooming, choose **Search this area** to search the visible neighborhood. Brand searches use
Google Text Search near the pin, then enforce the requested distance in SQL. Google Place IDs
identify branches. Results are joined to the nutrition catalog per request. Existing OSM and demo branch rows do not participate in Google-mode searches. No-key mode uses real OSM branch rows. Google bills searches and
map loads separately. See the [pricing list](https://developers.google.com/maps/billing-and-pricing/pricing).

## Run with Docker

```bash
docker compose up --build
```

The API is served on http://localhost:8000. The first boot downloads the RoBERTa model (~330 MB) and seeds demo data.

## Run without Docker (Windows-friendly)

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python dev_db.py          # starts embedded Postgres 16 + pgvector, writes DATABASE_URL to .env
```

`dev_db.py` writes the database address into `backend/.env` (creating it if needed) every time it
runs, because the embedded server can come back on a different port after a restart. It leaves a
`DATABASE_URL` that points at another database, such as the Docker one, alone. Pass `--no-env` to
only print the address, and stop the database with `dev_db.py --stop`. Add
`EMBEDDER_BACKEND=hash` to `.env` to skip the model download (see `.env.example`). Then:

```bash
.venv/Scripts/python -m app.db.seed
.venv/Scripts/python run.py
```

Frontend (http://localhost:5173, proxies `/api` to :8000):

```bash
cd frontend && npm install && npm run dev
```

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

DB tests use `TEST_DATABASE_URL` if set, otherwise an embedded `pgserver` instance.

## Optional authorized Instaloader ingestion

Each stage reads the previous stage's files under `backend/data/` (git-ignored), so you scrape
once and can re-run OCR or parsing as often as you like. Run these from `backend/`.

Only use this path when collection is authorized. Prefer the local import above when live
access is unavailable. A saved login or academic purpose alone does not establish authorization.

1. Log in to Instagram once using your own account. This saves a session file; you type your password into instaloader
   itself:

   ```bash
   .venv/Scripts/instaloader --login YOUR_IG_USERNAME
   ```

2. Scrape a small batch (posts already downloaded are skipped):

   ```bash
   .venv/Scripts/python -m app.ingest scrape --login YOUR_IG_USERNAME --max-new 15
   ```

3. OCR the images (needs Tesseract; cached per image):

   ```bash
   .venv/Scripts/python -m app.ingest ocr
   ```

4. Parse into review files:

   ```bash
   .venv/Scripts/python -m app.ingest parse
   ```

5. Open `data/review/reviewed.csv` in Excel. New rows start `skip/no`. Fix values, set `action` to `load` or `skip`, and
   `verified` to `yes` for rows you checked against the post. Re-running `parse` only appends
   new posts; it never overwrites your edits.
6. Validate, then load. Loading is all-or-nothing and also re-matches map pins to new brands:

   ```bash
   .venv/Scripts/python -m app.ingest load --dry-run
   ```

   ```bash
   .venv/Scripts/python -m app.ingest load
   ```

7. Once real data is in, set `SEED_DEMO_ITEMS=false` in `.env` and remove the made-up demo items:

   ```bash
   .venv/Scripts/python -m app.db.seed --purge-demo-items
   ```

`python -m app.ingest status` shows counts for every stage. After switching `EMBEDDER_BACKEND`
or `EMBEDDING_MODEL`, run `python -m app.ingest reembed`.

Stop on authentication failures or HTTP 401/429 and use local import while access is unavailable.
Do not bypass access controls, CAPTCHA or rate limits. Ask @anongcaloriesnito for permission,
original media or an authorized export. See the operator guide for session recovery and API options.

## Data honesty

Seed nutrition values are illustrative estimates and seed branch coordinates are approximate; both
are flagged in the database (`is_verified = false`, `osm_type = 'seed'`).

## Authorized nutrition collection

See [the collection and human review guide](docs/DATA-COLLECTION.md). The current pilot contains
36 real pending source-backed records across six PH brands; none is recommendation-eligible
until the operator supplies the specific approved IDs. Missing values stay null; exact hard
limits remain enforced.
