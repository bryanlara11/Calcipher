# Authorized nutrition collection and review

Run commands below from `backend/`, using `.venv/bin/python` on macOS/Linux. Keep source media,
receipts and review exports under the git-ignored `backend/data/`. Do not commit credentials or
an Instagram session. The application must never treat an extraction as human approval.

## Current source scope (2026-11-25)

Brands must originate from **@anongcaloriesnito**. The user subsequently authorized manual
internet research for nutrition and images after selecting brands from Instagram. Keep brand
origin and nutrient evidence separate: `source_post_url` identifies the Instagram origin and
`nutrition_source_url` identifies an external calorie source when used. Both are visible in cards.
Do not represent a secondary transcription as an official source or as personal source approval.

Current scope: 121 active pending records across 21 brands; six separate-source KFC rows remain
archived. Latest addition: 24 records across Crisostomo, CoCo Ichibanya, Dot Coffee and Conti's.
All four brands' posts were directly inspected on the selected public account. The official
Crisostomo PDF was rendered and labels checked against each retained item; only eight clear
records were kept. Other new values come from explicit Calorie Tsek Philippine item/variant
pages and are community-reported. Japan's CoCo nutrition figures were not used for PH dishes.
Dot snack summaries disagreed and were excluded. Conti's calories are per piece, even where a
source displays the price of a box. No current prices were imported.

Known active fields: 121 calories, 11 protein, 8 carbs, 8 fat. There are 8 official-source and 113
community-reported rows, zero human-approved rows and zero checked current PHP prices. Missing
portion sizes remain unknown; do not divide menu-dish totals into assumed person servings.

The scope audit supports both older credit-linked HTML snapshots and new paired evidence:
retained nutrition snapshot plus a matching browser-observed Instagram brand origin. It checks
hash integrity, source account, brand and post identity, then rematches real branches. The new
metadata is evidence of brand discovery, not nutrition approval. Include every original batch
when running the audit, since rows absent from allowed evidence become inactive. Use local
original batches; published JSON omits machine-specific snapshot paths.

Nine original Instagram posts have been inspected across the project, including the four new
brand origins. Not every original slide/item was inspected. The bounded Instaloader attempt
still failed; anonymous Python Instagram retrieval remains unproven. No specific human-approved
IDs have been supplied. Previous exports and hashes are historical versions.

See `data/catalog/README.md` for the expanded dataset hash, evidence and import commands.
Raw source/permission evidence remains local. Public external images have attribution links;
public visibility is not represented as an open-content license.

## Choose an authorized method

Check the source's current access rules and your permission before each new collection batch.
Public visibility or attribution alone is not permission to republish. Keep a reference recording
the source/accounts, allowed methods, media/facts reuse, attribution and any limits.

The [official Instagram Login API](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login)
requires an eligible professional account and its authorized OAuth grant. There is no configured
owner grant here; the API was investigated but not exercised. An Instagram Login setup does
not require a linked Facebook Page. Other Meta API configurations have different prerequisites.
No authentication was needed for the public pages observed in this pilot, but that does not
establish continued anonymous Python access. An owner's content permission does not remove
platform authentication or API restrictions.

If explicitly permitted and supported, the existing bounded scraper is:

```sh
.venv/bin/python -m app.ingest --data-dir data/new-batch scrape --profile anongcaloriesnito --max-new 1 --sleep 5
```

Stop on access refusal. An optional `--login` / `--session-file` can use your own authorized
session; never copy another person's cookies or bypass CAPTCHA/login requirements. See
[Instaloader's documentation](https://instaloader.github.io/basic-usage.html).

For the working secondary-source method, select explicit item URLs in the example format.
Do not run the entire example unless that batch is authorized. The collector checks robots.txt,
uses an identified User-Agent, spaces requests, caps batches at 50, rejects redirects/restrictions
and preserves snapshots with hashes. It verifies the brand, Philippine source context and
visible nutrition against JSON-LD. New output paths preserve previous evidence.

```sh
.venv/bin/python -m app.collect_sources --urls-file ../docs/examples/pilot-source-urls.json --output data/new-batch/candidates.json --permission-reference YOUR_DOCUMENTED_REFERENCE
```

The collector was exercised against bounded public item batches. Snapshot parsing and source-scope checks are separate from mocked tests. All current records remain pending.
Review [terms](https://worththecals.com/terms) and [methodology](https://worththecals.com/methodology).

## Manual originals, captions and OCR

Open the original post normally. Record URL, account, date, full caption and ordered original
slides. Preserve each item's slide association. Transcribe only explicitly reported values;
keep absent macros, serving basis and prices null. Never infer a serving from the item name or
divide container totals unless the source explicitly supports that operation. Mark the source
official, community-reported or estimated based on the evidence, not the presence of review.

Use the existing manifest example in `docs/examples/local-posts.json`. Paths resolve relative to the manifest.

```sh
.venv/bin/python -m app.ingest --data-dir data/new-batch import-local --manifest /absolute/path/to/manifest.json
.venv/bin/python -m app.ingest --data-dir data/new-batch ocr --lang eng --psm 6
.venv/bin/python -m app.ingest --data-dir data/new-batch parse
```

OCR caches are tied to image SHA-256 and engine/preprocessing identity. Slides are parsed
independently. The parser flags multi-size table columns for review rather than assuming a
single serving. `parsed.csv` is machine output; `reviewed.csv` belongs to the reviewer and is
not rewritten. Initially rows are skip/no.

For difficult labels, annotate normalized crop coordinates and an item hint explicitly:

```sh
.venv/bin/python -m app.ocr_regions --raw-dir data/source-pilot-2026-11-25/raw --manifest ../docs/examples/pilot-ocr-regions.json --output data/new-batch/region-observations.json --psm 6
```

These are assisted observations, never approval. The real eight-slide carousel produced seven
unsafe full-image proposals, all skipped. Three assisted regions recovered a calorie candidate
for only one label (110 kcal); the other two missed their calories. Small text, perspective,
multi-item layouts, prices and coffee columns caused errors. Item hints are manual annotations,
not OCR recognition. A human-verified field accuracy benchmark is still pending. No automatic
OCR accuracy claim or production approval follows from these results.

## Prepare, review and approve a fixed dataset

The header-only `docs/examples/manual-review.template.csv` also supports direct manual review.
Candidate JSON preserves each value's URL in `field_sources`, original/secondary source URLs,
classification, collection date, media filenames and snapshot hashes. Missing values are JSON
null. The current account-scoped review export exposes all 97 active record IDs and source links.

```sh
.venv/bin/python -m app.services.source_review --candidates data/new-batch/candidates.json --output data/new-batch/review/pending.csv
```

The command prints the exact dataset SHA-256. A human must personally inspect the sources and
name the approved IDs. Copy `docs/examples/approval-receipt.template.json`, fill the exact hash,
reviewer, timestamp with timezone, review reference and only approved IDs. An empty list approves
nothing. Do not use example IDs as approval or alter the JSON after review.

```sh
.venv/bin/python -m app.services.source_review --candidates data/new-batch/candidates.json --output data/new-batch/review/approved.csv --approval-receipt data/new-batch/approval.json
.venv/bin/python -m app.ingest --data-dir data/new-batch load --file data/new-batch/review/approved.csv --dry-run
.venv/bin/python -m app.ingest --data-dir data/new-batch load --file data/new-batch/review/approved.csv
.venv/bin/python -m app.ingest status
```

Pending records may be stored for review. Recommendation retrieval and default map/menu
responses exclude them. Chat may expose exact-filtered pending records in a separate
`catalog_matches` array when no approved result exists; these are labelled previews, never
approved recommendations. Its `results` array and factual recommendation generator remain
approved-only. Nearby explicitly requests `include_pending=true` to browse catalog
locations and menu records with pending-review labels; it keeps `include_unmatched=false` so
unrelated places stay hidden. `has_calorie_data` and approved nutrition counts still describe
reviewed records only. Branch matching can link pending catalog brands without approving facts. Approval preserves community/estimated classification; it does not make a
community source official. The load embeds records with the active model and rematches branches
in the same transaction. API brand discovery refreshes within five minutes; restart after an
operator-controlled import if an immediate refresh is needed.

## Corrections, duplicates and prices

The identity `(brand_slug, food_item, serving_size)` is unique with NULLS NOT DISTINCT, including
unknown servings. Repeated imports do not create duplicates. Changing a serving basis changes
identity: review whether an older row should be retained before importing a replacement.
Conflicting CSV duplicates fail validation. Existing verified corrections are protected by
default, including from other verified imports. After personally approving a correction, use
`load --file ... --allow-reviewed-updates` explicitly. An unverified row still cannot overwrite
a verified row. Keep the previous source/review evidence rather than silently replacing it.

This pilot source-review path rejects prices. The existing manually reviewed CSV path can load
a checked `price_php` and `price_source_url`; use only a price supported at the applicable scope
and collection time. The catalog has no branch-specific price model. Do not load a branch price
as a universal chain price. All pilot prices stay null; budget-filtered recommendations exclude
them. Unknown protein is similarly excluded when a minimum protein is requested. Calorie,
budget and distance limits remain exact; an empty result does not widen them automatically.

Nutrition is matched by brand to real OSM branches; a match does not prove current item stock
or availability at every branch. The interface states that limitation. OSM discovery remains
available without keys. Google maps/discovery require both matching browser and server keys.
Gemini is optional; deterministic parsing and grounded templates remain available without it.

## Validation and evidence boundaries

Backend: `python -m pytest -q`; frontend: `npm test -- --run`, `npm run lint`, `npm run build`.
The 2026-11-25 run passed 302 backend and 30 frontend tests, lint and build. Test fixtures and
mocked network responses prove safeguards, not live acquisition or live recommendation coverage.
Live source logs, image OCR, source consistency checks and persistent DB/API checks are reported
separately. No real approved recommendation demonstration can be claimed until approved IDs
are supplied and promoted. No affordability success can be claimed with zero checked prices.

## Expanded catalog batch, 2026-11-25

A further 24 source-backed records were collected with the same bounded collector and imported
as pending: six each for Jollibee, Burger King, Popeyes and World Chicken. Visible calories and
structured data were checked by the collector; no missing macros, servings or prices were inferred.
The persistent catalog now has 60 records across ten brands, with zero approvals and zero checked
current PHP prices. The original 36-record snapshot remains intact. The new batch SHA-256 is
`11e88f4ac88d88b3e13bb108a01afa2524e2a348d6d07a3f0cf1de5cbf776ca8`.

To reproduce a fresh pending batch from explicit public source URLs, from `backend/`:

```sh
.venv/bin/python -m app.collect_sources --urls-file ../docs/examples/catalog-expansion-source-urls.json --output data/expansion-recheck/candidates.json --permission-reference YOUR_DOCUMENTED_REFERENCE
.venv/bin/python -m app.services.source_review --candidates data/expansion-recheck/candidates.json --output data/expansion-recheck/review/pending.csv
.venv/bin/python -m app.ingest --data-dir data/expansion-recheck load --file data/expansion-recheck/review/pending.csv --dry-run
.venv/bin/python -m app.ingest --data-dir data/expansion-recheck load --file data/expansion-recheck/review/pending.csv
```

This stores pending records and updates branch matching; it does not approve recommendations.
If the source changes, preserve the new snapshot separately and review that version explicitly.
