# Account-scoped pending catalog

`anongcaloriesnito.pending.json` contains 97 pending nutrition records across 17 Philippine
restaurant/store brands credited to @anongcaloriesnito. It includes cafés and sit-down options.
Each entry retains its original Instagram post, secondary transcription URL, field sources and
snapshot SHA-256. Machine-specific snapshot paths and private source/permission receipts are
omitted. Raw snapshots remain with the original operator's local collection batches.

This is a new filtered dataset, not a replacement for the immutable original pilot review hash.
Its SHA-256 is `b6e07748cc88a6521622da9e59187cc1eca3fc6a7f91df855ed82f7ccc9f1659`.
All entries are community-reported and pending; permission to reuse was attested by the operator,
not independently verified. No human approval IDs were supplied. Three records contain reported
protein; carbohydrate/fat/current-price values are unknown. Do not fill them with zero.

From `backend/`, prepare a pending CSV and validate before loading:

```sh
.venv/bin/python -m app.services.source_review --candidates ../data/catalog/anongcaloriesnito.pending.json --output data/account-catalog/review/reviewed.csv
.venv/bin/python -m app.ingest --data-dir data/account-catalog load --dry-run
.venv/bin/python -m app.ingest --data-dir data/account-catalog load
```

Loading calculates embeddings using the configured model. Repeated loads use existing duplicate
protection. On an existing database, archive rows outside the selected source with the documented
snapshot-backed scope audit; loading this dataset alone does not remove older rows. The current
operator database has six archived official KFC rows and 97 active pending rows. Archive status
is separate from review and is not reset by ordinary upsert.

For further collection, use the four explicit source-URL example files in `docs/examples/`.
The collector stops when the source is not credited to the selected Instagram account. Original
posts still require human checking; normal browser inspection of five posts does not verify
all 97 records. Promotion requires selected record IDs in a human receipt bound to this dataset
hash, using the workflow in `docs/DATA-COLLECTION.md`.
