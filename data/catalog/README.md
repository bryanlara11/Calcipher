# Instagram-origin brands with sourced nutrition

`anongcaloriesnito.pending.json` contains **121 pending records across 21 Philippine brands**.
Brands are selected from @anongcaloriesnito. Nutrition may come from the original posts, an
official Philippine menu, or a clearly labelled web transcription. No brand is added solely
because it appears on a general calorie website. Every new brand's original Instagram post is
listed in `instagram-brand-origins.json`.

The latest batch, `internet-expansion.pending.json`, adds 24 records: Crisostomo (8), CoCo
Ichibanya (6), Dot Coffee (6) and Conti's (4). Crisostomo's eight entries were manually matched
to labels on rendered official-menu pages. Other new calorie values are community-reported
Calorie Tsek transcriptions, with separate Instagram brand origins. They remain pending.
CoCo values are Philippine listings; the Japanese nutrition table was not substituted. CoCo's
rice weight/inclusion is not established by the calorie source. Conti's pastry calories are per
piece; box prices are not meal prices. Dot drink size/temperature variants stay separate;
conflicting snack summaries were omitted.

Current dataset SHA-256: `0b88e435ed5f75bd5f6ba6239cefaf56f414fb54cacfe0a9b8153f52580981be`.
This is a new expanded version. Earlier 36/60/97-record exports and their hashes remain historical.
There are 8 official-source and 113 community-reported records, all pending. Known nutrient
coverage: 121 calories, 11 protein, 8 carbohydrate, 8 fat, zero checked current PHP prices.
Missing fields remain null. Permission to collect/reuse the original account material was
attested by the operator; separate internet research was explicitly requested. Public availability
of external images is not represented as an open-content license. Each image links to its source.

Machine-specific snapshot paths, raw evidence and private receipts are omitted from Git. Raw
snapshots, public-browser origin observations and hashes remain in the operator's local batches.
Neither manual transcription nor source permission is human approval for recommendations.

From `backend/`, create a new review path rather than overwriting an earlier review version:

```sh
.venv/bin/python -m app.services.source_review --candidates ../data/catalog/anongcaloriesnito.pending.json --output data/account-internet-2026-11-25/review/reviewed.csv
.venv/bin/python -m app.ingest --data-dir data/account-internet-2026-11-25 load --dry-run
.venv/bin/python -m app.ingest --data-dir data/account-internet-2026-11-25 load
```

Loading calculates embeddings and uses duplicate protection. It does not remove other existing
rows. The current DB has 121 active pending rows and six archived KFC rows. Ordinary upsert does
not revive archived rows. The snapshot-backed scope audit must include all original batches,
including the internet-enrichment batch. It validates both the nutrition snapshot and the
matching Instagram brand-origin observation; it never changes review flags. The distributable
JSON omits local snapshot paths, so use the original retained batches for a scope re-audit.

Promotion requires selected record IDs and a human receipt bound to the dataset hash. See
`docs/DATA-COLLECTION.md`. No specific approved IDs have been supplied.
