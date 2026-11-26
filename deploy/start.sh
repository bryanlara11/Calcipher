#!/bin/sh
# Boot sequence for the hosted image: create schema, load the published pending catalogs, serve.
# Every step is idempotent (upserts), so it is safe on each restart and on a fresh database.
set -e

work="${TMPDIR:-/tmp}/calcipher-load"
rm -rf "$work"

python -m app.db.seed

# Each committed catalog goes through the same review checks and loader, all as pending review.
for catalog in anongcaloriesnito kfc-official manual-intake; do
    python -m app.services.source_review \
        --candidates "catalog/$catalog.pending.json" \
        --output "$work/$catalog/review/reviewed.csv"
    python -m app.ingest --data-dir "$work/$catalog" load
done

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips='*'
