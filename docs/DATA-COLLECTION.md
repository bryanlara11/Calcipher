# Authorized nutrition collection and review

Run commands below from `backend/`, using `.venv/bin/python` on macOS/Linux. Keep source media,
receipts and review exports under the git-ignored `backend/data/`. Do not commit credentials or
an Instagram session. The application must never treat an extraction as human approval.

## Evidence from the 2026-11-25 pilot

- The operator attested permission for both sources, the listed collection methods and reuse.
  This is an operator attestation, not an independently inspected owner permission document.
- One bounded Instaloader attempt failed with `BadResponseException: Fetching Post metadata
  failed`. Python Instagram media/caption retrieval was **not demonstrated**. The exception is
  now handled as a clean stop. Do not retry around an authentication/rate-limit restriction.
- Two original Instagram posts were inspected in a normal public browser. One complete
  eight-image carousel, its caption, date and post URL were retrieved and imported locally:
  <https://www.instagram.com/p/DYUCaaOklAh/>. Army Navy post
  <https://www.instagram.com/p/DUi0Ptmku0e/> was only partially inspected; its complete media
  download was not demonstrated. Do not count it as a complete retrieved post.
- Secondary-source collection inspected six brand pages and 54 item pages. Thirty-six retained
  candidates cover 7-Eleven, Army Navy, Chowking, Mang Inasal, McDonald's and KFC. Visible HTML
  and JSON-LD agree for 36 calorie, nine protein, six carbohydrate and six fat values. Six KFC
  records were additionally compared with the original official table:
  <https://corporate.kfc.com.ph/nutrition-and-allergen-information/>.
- Thirty candidates are community-reported and six official. Thirty serving sizes remain null;
  the six KFC records retain the table's explicit `1 serving`. There are zero checked current
  PHP prices. Historical photographed prices were deliberately not imported as current prices.
- All 36 were actually loaded into the persistent catalog as **pending**, not merely saved to
  files. None is approved while the operator's specific approved record IDs are outstanding.
  The minimum of 30 approved records has therefore not yet been achieved.

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

The module was actually exercised against one public 7-Eleven item: one collected, zero approved.
Its parser also checked all 36 retained pilot snapshots. These are separate from mocked tests.
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
null. The pilot review page exposes all 36 record IDs and links for personal source checking.

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

Pending records may be stored for review, but retrieval, map nutrition counts and brand menus
all exclude them. Approval preserves community/estimated classification; it does not make a
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
