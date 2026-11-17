# CalCipher

Hybrid RAG for Philippine restaurant calories: nutrition data from **@anongcaloriesnito** (OCR) is
joined to real restaurant locations from the free **OpenStreetMap Overpass API**, filtered by hard
SQL constraints (calories, protein, distance), and ranked with **pgvector**.

Architecture, invariants and status: see [AGENTS.md](AGENTS.md).

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
.venv/Scripts/python dev_db.py          # starts embedded Postgres 16 + pgvector, prints DATABASE_URL
```

Put the printed URL in `backend/.env` as `DATABASE_URL=...` (see `.env.example`). Add
`EMBEDDER_BACKEND=hash` to skip the model download. Then:

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

## Load real @anongcaloriesnito data

Each stage reads the previous stage's files under `backend/data/` (git-ignored), so you scrape
once and can re-run OCR or parsing as often as you like. Run these from `backend/`.

1. Log in to Instagram once. This saves a session file; you type your password into instaloader
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

5. Open `data/review/reviewed.csv` in Excel. Fix values, set `action` to `load` or `skip`, and
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

Instagram's terms prohibit automated scraping: keep batches small and use the data for academic
research only. Asking @anongcaloriesnito for permission or a data export is the safer route.

## Data honesty

Seed nutrition values are illustrative estimates and seed branch coordinates are approximate; both
are flagged in the database (`is_verified = false`, `osm_type = 'seed'`).
