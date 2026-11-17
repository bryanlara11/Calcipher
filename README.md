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

## Ingest real posts

```bash
cd backend
.venv/Scripts/python -m app.services.ingestion_ocr --max-posts 20 --dry-run
```

Requires the Tesseract binary. Instagram throttles anonymous access; see the module docstring for
`--login`. For academic use; respect Instagram's terms.

## Data honesty

Seed nutrition values are illustrative estimates and seed branch coordinates are approximate; both
are flagged in the database (`is_verified = false`, `osm_type = 'seed'`).
