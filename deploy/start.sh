#!/bin/sh
# Boot sequence for the hosted image: create schema, load the published pending catalog, serve.
# Every step is idempotent (upserts), so it is safe on each restart and on a fresh database.
set -e

work="${TMPDIR:-/tmp}/calcipher-load"
rm -rf "$work"

python -m app.db.seed
python -m app.services.source_review \
    --candidates catalog/anongcaloriesnito.pending.json \
    --output "$work/review/reviewed.csv"
python -m app.ingest --data-dir "$work" load

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips='*'
