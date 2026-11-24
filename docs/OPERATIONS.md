# CalCipher operator guide

## Install and configure

Use your local `Calcipher` checkout on `dpsahagon/instagram-ingestion-fix`. `origin` remains `https://github.com/bryanlara11/Calcipher.git`.
Start commands below from that repository root unless a different directory is specified.

Tested: macOS arm64, Python 3.12.0, Git 2.50.1, Node 22.23.5 LTS, npm 11.21.3, Tesseract 5.5.4,
Instaloader 4.16, PyTesseract 0.3.13, pgserver 0.1.4. Node 22 LTS is installed alongside the previous Node 23; no global executable links were changed. `requirements-tested.txt` records this Mac's Python
versions as installation constraints, rather than promising identical wheels on every platform.

```bash
brew install tesseract node@22
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c requirements-tested.txt
.venv/bin/python -m pip check
tesseract --version
tesseract --list-langs
.venv/bin/python dev_db.py
```

`dev_db.py` creates `backend/.env` if missing. Preserve its exact `DATABASE_URL`: on this Mac
pgserver uses a Unix socket, not a TCP port. If `.env` points at another database, the script
preserves that value; deliberately use the printed address to switch. Do not overwrite existing
configuration. Set `SEED_DEMO_ITEMS=false` and `SEED_DEMO_BRANCHES=false` in `.env` for a real-data
pilot; the audit-created file already does this. The embedded database supplies PostgreSQL 16 and
pgvector; pg_trgm is optional and absent. Stop it with `.venv/bin/python dev_db.py --stop`.

Production embeddings remain `sentence-transformers/all-distilroberta-v1` (768 dimensions), with
a first model download. For an offline lexical test/demo, set `EMBEDDER_BACKEND=hash` in `.env`
consistently for load and API. Whenever switching models/backends, run:

```bash
.venv/bin/python -m app.ingest reembed --all
```

Do not use `all-roberta-large-v1` with VECTOR(768). Schema initialization adds nullable prices and
their source/verification constraint without dropping existing catalog rows. Back up an important
database before deployment; this audit used new local/test databases, not Bryan's database.
On Windows replace `.venv/bin/python` with `.venv/Scripts/python` and retain `run.py` for psycopg.

## Source options, in priority order

1. Ask the owner for permission, original-resolution carousel images, exact captions, URLs,
   and any structured nutrition data. Keep permission/export evidence locally. No owner contact
   or permission was completed in this audit.
2. Import that authorized material locally, as below. This is the practical academic workflow
   because OCR/review can run repeatedly without depending on Instagram access. A native export
   must be mapped into the manifest; arbitrary Meta export archives are not automatically decoded.
3. Retain Instaloader only for collection where owner and platform authorization apply. A saved
   login does not establish permission or guarantee access.

The official Instagram Login API serves professional accounts and authorized app workflows;
it is not a blanket downloader for arbitrary public users. The source account type, owner app
authorization and applicable permissions were not established here. See
[Meta's product documentation](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login)
and [IG User requirements](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/v21.0/).
An owner-supplied export avoids needing a new Meta app for this prototype.

## Import authorized local media

Run ingestion commands from `backend/`. Arrange permitted files locally:

```text
source-data/                  # ignored by Git
  posts.json
  post/
    slide-1.jpg
    slide-2.jpg
```

Copy `../docs/examples/local-posts.json` to `source-data/posts.json`, replace placeholders, and
list all carousel slides in source order. Required: stable `shortcode`, nonempty provenance
`url`, and nonempty `images`. Optional: exact `caption`, `profile`, ISO `date_utc`, `source_reference`.
For non-Instagram material, use a stable ID and honest provenance reference, not an invented post.
Example shape (not real source data):

```json
{"posts":[{
  "shortcode":"OWNER_SUPPLIED_ID",
  "url":"https://www.instagram.com/p/OWNER_SUPPLIED_ID/",
  "caption":"Exact original caption",
  "source_reference":"Owner-provided originals; local permission record",
  "images":["post/slide-1.jpg","post/slide-2.jpg"]
}]}
```

```bash
.venv/bin/python -m app.ingest import-local --manifest source-data/posts.json
.venv/bin/python -m app.ingest ocr --lang eng --psm 6
.venv/bin/python -m app.ingest parse
```

JPEG, PNG and WebP are supported. Paths must resolve inside the manifest folder. Images and the
entire manifest are validated before copying; no image URLs are fetched. Output:
`data/raw/<id>_<slide>.<extension>` and `<id>.meta.json` with caption, ordered images, date and
source references. Metadata is written last and atomically. Identical reimports skip; changed
metadata/media for an existing ID fail without overwriting evidence or review. Compare better
replacement media in a separate data directory and reconcile existing review rows deliberately.

The global directory option goes **before** the command, consistently for every stage:

```bash
.venv/bin/python -m app.ingest --data-dir data/pilot import-local --manifest source-data/posts.json
.venv/bin/python -m app.ingest --data-dir data/pilot ocr
.venv/bin/python -m app.ingest --data-dir data/pilot parse
.venv/bin/python -m app.ingest --data-dir data/pilot load --dry-run
```

## OCR, parsing and human review

OCR retains PyTesseract, with EXIF correction, bounded upscaling, denoising, binarization and
polarity correction. `--psm 6` handles a block or simple table rows; try `--psm 11` for sparse
graphics or `--psm 3` for automatic segmentation. Inspect every table's row/column associations.
Original-resolution sources or authorized crops per menu section help; upscaling cannot recover
lost pixels. After changing OCR options:

```bash
.venv/bin/python -m app.ingest ocr --psm 11 --force
.venv/bin/python -m app.ingest parse
```

`data/ocr/<image>.json` contains `image`, `sha256`, `engine`, `text`. Cache identity includes
Tesseract version, language, segmentation, preprocessing version and image hash. Corrupt caches
are recomputed; stale image/preprocessing output is not parsed. A failed refresh cannot expose an
old result. Slide parsing is independent; caption values are parsed separately. Conflicting
repeated items and ambiguous calorie values are flagged for review.

`parsed.csv` is regenerated as proposals. `reviewed.csv` is human-owned: only previously unseen
complete posts are appended. Posts missing any valid slide OCR remain in the proposals and
missing-OCR summary until OCR completes. Complete posts producing no items get a skipped
placeholder for manual transcription. Parsing never overwrites existing reviewed corrections.

Back up `data/review/reviewed.csv`, then open it in a UTF-8-aware editor. Columns:

```text
action,verified,shortcode,source_post_url,brand_name,brand_slug,food_item,serving_size,
calories,protein_g,carbs_g,fat_g,is_suspect,warnings,price_php,price_source_url,source_images,provenance_json
```

1. All generated rows start `action=skip, verified=no`. Compare each field to originals/captions.
   Correct brand, food, serving and numbers; explicitly set `action=load` for desired rows.
2. Keep absent macros/prices blank (null). Blank serving is stored as SQL NULL.
   Missing calories cannot load: keep the placeholder skipped until evidence supplies them.
   Explicit zero is accepted. Fractional calorie proposals are flagged because the existing
   database requires whole kcal; a human must resolve the representation from the original.
3. Fix conflicting/ambiguous/Atwater warnings or retain `skip`. Suspect unverified rows cannot
   load. Set `verified=yes` only after a human checks evidence. It attests to the transcription,
   not independent scientific validation of the source. An intentionally unverified load remains
   unverified in the database and is excluded from chat recommendations and approved nutrition counts. Nearby can explicitly browse pending catalog locations and menus, with review labels.
4. Optional prices require PHP (at most two decimals), an HTTP(S) `price_source_url`, and
   `verified=yes` after checking the same item/serving against that price source. Do not infer
   prices from calories, another portion, or a Places price category. Source prices may vary
   across branches. Unknown prices are excluded from budget searches, never treated as zero.

Synthetic example row (not nutritional evidence):

```csv
action,verified,shortcode,source_post_url,brand_name,brand_slug,food_item,serving_size,calories,protein_g,carbs_g,fat_g,is_suspect,warnings,price_php,price_source_url,source_images,provenance_json
load,no,FIXTURE,urn:calcipher:test,Fixture Kitchen,fixture kitchen,Fixture Bowl,1 bowl,400,20,,,no,,,
```

Old CSVs without price columns remain readable and retain their original header when appended.
To enter prices, add the two price columns explicitly in your editor. Do not replace reviewed.csv
with parsed.csv. Revisit reviewed posts by comparing proposals and editing the existing review
after a backup. Resolve duplicate `(brand_slug, food_item, serving_size)` rows before loading.
Changing a food/serving identity does not automatically delete the old database identity.

```bash
.venv/bin/python -m app.ingest load --dry-run
.venv/bin/python -m app.ingest load
.venv/bin/python -m app.ingest status
```

Dry-run validates without database/model I/O. Any error blocks the entire file. A valid load
embeds canonical contexts, upserts and rematches legacy branches in one transaction. Repeated
loads reuse the unique identity. Unverified writes never overwrite verified records; an explicit
human re-audit can update them. Google locations match catalog brands at request time. Allow the
brand registry five minutes to refresh, or restart the API after loading a new brand for a demo.

## Optional authorized Instaloader access

Only use where owner permission and platform authorization apply. Use your own saved session;
enter your password interactively in Instaloader, never in code/CSV/shell arguments:

```bash
.venv/bin/instaloader --login YOUR_IG_USERNAME
.venv/bin/python -m app.ingest scrape --login YOUR_IG_USERNAME --max-new 5
.venv/bin/python -m app.ingest scrape --login YOUR_IG_USERNAME --session-file /absolute/path/to/your.session --max-new 5
```

The last command illustrates an alternative session path, not an extra required run. The
collector checks session identity before enumeration, stops on authentication or 401/429
failures, and reports completed posts. It uses one connection attempt rather than repeatedly
retrying a refusal. Image count/decodability are checked before completion metadata is written;
completed posts skip and incomplete downloads remain retryable when access is available.

401 may reflect authentication/session or platform access enforcement; 429 is rate limiting.
Neither proves a code bug. Do not repeatedly restart to probe a block, automate CAPTCHA,
rotate proxies/accounts, borrow sessions, or evade restrictions. Follow normal Instagram prompts
manually and defer authorized requests until permitted; use local import meanwhile. Academic
purpose and small batches do not create an authorization exception.

[Instaloader troubleshooting](https://instaloader.github.io/troubleshooting.html) explains rate
controller assumptions and rapid restarts; its [API reference](https://instaloader.github.io/module/instaloader.html)
documents sessions, carousel downloads and fatal status/attempt settings. No successful live
Instagram collection was executed in this audit.

## Run and demonstrate

Keep the database URL. For optional Google mode, add `GOOGLE_PLACES_API_KEY` to `backend/.env`, restricted to Places API
(New). Add a different `VITE_GOOGLE_MAPS_API_KEY` to `frontend/.env`, restricted to Maps JavaScript
API and your local referrers (`http://127.0.0.1:5173/*`, `http://localhost:5173/*`). Enable billing
and both APIs in Cloud. Optional `GEMINI_API_KEY` enables selection/intent extraction; regex and
template responses work without it. The LLM can select only a retrieved item ID; code renders
all factual text. Unknown IDs or extra claims cause fallback.

No Google keys are needed for the default OpenStreetMap map. Leave the browser key blank and
`OVERPASS_ENABLED=true` for OSM restaurant discovery. Google mode activates only when the browser
key and server Places key are both present. See **Settings → Map connection**. Capability flags
are available at `/api/v1/map-config`; the server Places key is never exposed. Map/chat requests
carry `X-CalCipher-Map-Provider: osm|google`, including through the Vite proxy. No-key mode uses
the existing 24-hour database cache, one request at a time, public mirror fallback and failure
cooldown. Philippines searches now use `https://ovp.dresel.systems/api-as/interpreter` first (Asia regional data), with global Overpass and Private.coffee fallbacks. Live map and widened chat discovery succeeded on 2026-11-24. The old Kumi mirror has moved to `overpass.private.coffee`.

The interface has `#/search`, `#/nearby`, `#/chat`, `#/settings` pages. Search fields and chat
history survive navigation until refresh; there is no persistent storage of restaurant listings.
The initial radius is 1 km. Open area controls to change it; map/list share the same results.
A discovery outage leaves the basemap usable and reports that listings are unavailable. Location
permission is requested only after the explicit location button.

Backend terminal, from repository root:

```bash
cd backend
.venv/bin/python dev_db.py
SEED_DEMO_ITEMS=false SEED_DEMO_BRANCHES=false .venv/bin/python -m app.db.seed
.venv/bin/python run.py
```

Frontend terminal, from repository root:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Verification terminal:

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail 'http://127.0.0.1:8000/api/v1/brands/jollibee/items'
curl --fail 'http://127.0.0.1:8000/api/v1/map-items?lat=14.5785&lng=120.9850&radius_km=2'
curl --fail -H 'Content-Type: application/json' \
  -d '{"message":"chicken under 500 kcal under PHP150 within 2 km","lat":14.5785,"lng":120.9850}' \
  http://127.0.0.1:8000/api/v1/chat
```

Use a brand/meal actually in your review; the Jollibee example does not imply real data was
loaded. Open `http://127.0.0.1:5173`, select an area with a known branch, open its menu and chat,
and inspect serving, verification and source information. Test a budget only with price evidence.
The inspector shows nutritional and PHP constraints. Calorie, protein, budget and radius caps
remain exact; the application does not widen them automatically. Production Places discovery requires at least five reported ratings
and one search returns up to 20 listings before filtering, so the map is not an exhaustive census.

For a labelled illustrative demo, the existing seed command supports `SEED_DEMO_ITEMS=true`.
Those values are estimates with no price evidence. Keep demo/synthetic data separate from the
real-data submission. Audit fixtures were not loaded into the persistent dev database.

Docker remains `docker compose up --build` (not built in this audit). Configure keys and start
the frontend separately. Compose seeds according to `SEED_DEMO_ITEMS`; it has no `/app/data`
mount. Add persistent data/source mounts before container ingestion or artifacts disappear when
the container is replaced. Local Python ingestion is the validated handoff path.

## Tests and troubleshooting

```bash
cd backend
.venv/bin/python -m pytest -q -ra
.venv/bin/python -m pytest -q tests/test_local_ingestion.py -ra
.venv/bin/python -m pip check
cd ../frontend
npm run build
npm audit
```

Tests use synthetic media, mocked network/LLM responses, real Tesseract where installed and
embedded PostgreSQL/pgvector. Never point `TEST_DATABASE_URL` at a valuable database: existing
test fixtures truncate catalog and branch tables. Use only a disposable test database.

| Symptom | Action |
| --- | --- |
| Tesseract missing | `brew install tesseract`; check PATH or set `TESSERACT_CMD=/opt/homebrew/bin/tesseract`. |
| Language missing | Check `tesseract --list-langs`; use `eng` or install `tesseract-lang` before `eng+fil`. |
| No items / merged names | Inspect originals, captions and cached text; manually transcribe/correct unsupported layouts. Do not guess. |
| Brand absent | Supply `Brand: ...` in metadata only when supported by the source, or fill the review manually. |
| Missing OCR | Successfully OCR every listed image, then rerun parse; incomplete posts were not frozen in review. |
| Suspect values | Correct against evidence or retain `skip`; do not promote unchecked OCR to verified facts. |
| Comma/precision error | Use decimal dots and at most two decimal places for macros/PHP; only proper thousands commas are accepted. |
| Import conflict | Existing evidence was preserved; compare replacements separately and reconcile reviewed corrections. |
| DB connection error | Start `dev_db.py`, check its exact URL against `.env`, or start the configured PG+pgvector server. |
| vector missing | Use pgserver or PostgreSQL with pgvector, not SQLite/ordinary PostgreSQL without vector. |
| pg_trgm warning | Expected on pgserver; only the optional legacy trigram index is skipped. |
| Stale embeddings | Match load/API backend settings, then `python -m app.ingest reembed --all`. |
| Map / discovery | No keys: OSM tiles + cached Overpass discovery. Public endpoint timeout: show cached places and a visible warning; retry later. Optional Google mode needs both separate keys, APIs and billing, then an API/Vite restart. Settings shows configuration presence only, not credential validity. |
| Places 429 | Respect Retry-After and persisted cooldown; check project quota/billing. Do not keep restarting. |
| No budget results | Unknown prices are excluded. Supply verified price evidence or change the cap. |
| Model download unavailable | Use a cached model or consistently use hash plus re-embedding, labelled as a lexical demo. |
| Gemini unavailable | Regex/template fallback works; no live Gemini call was validated here. |

The three previous dependency findings are addressed by Vite 6.4.6 and PostCSS 8.5.33.
The React plugin already supports Vite 6; React and Tailwind were retained. Current security
and execution evidence is in [AUDIT.md](AUDIT.md). The added test runner is patched Vitest 4.1.12.

## Branded interface and appearance

The supplied avocado logo is used for navigation, Home, splash and browser/app icons, with
transparency and proportions preserved. Both themes use avocado accent #5B8219. Light surfaces
are white; Dark uses black backgrounds, shaded panels, white headings and gray body text. Maps
retain natural provider colors. Navigation has no numbered labels or repeated brand text.

Home explains the workflow and is the only navigation entry to About menu data. Search finds
meals and keeps its own results; Nearby groups branches by brand; Chat shares Nearby's search
area. Meal Plan saves dated Breakfast/Lunch/Dinner/Snack choices and reports known nutrition
with missing values called out. Favorites stores restaurants locally. Both use browser storage,
not an account or cloud backup. Settings contains appearance, map connection and privacy only.

Use **Settings → Appearance** for Light, Dark or System. Preference is saved under
`calcipher.theme`; System responds to OS changes. Storage failure falls back to the current visit.
The mobile bottom navigation and responsive panels support phone, tablet and laptop breakpoints.
Pending records never acquire an approval seal: a reviewed record must also meet the selected
calorie/protein goals before displaying the Healthy badge.

Frontend verification uses Node 22 LTS. npm 10 encountered an optional-peer resolver error with
current test tooling during setup; the verified toolchain uses npm 11 without a global install:

```bash
cd frontend
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
npx --yes npm@11.21.3 ci
npm run lint
npm test
npm run build
npm audit
npm audit --omit=dev
npm run dev -- --host 127.0.0.1
```

The new tests use Vitest/jsdom, React Testing Library and axe-core structural checks. Contrast is
checked against the actual shared text/control token pairs; color-contrast is unavailable in
jsdom and is not represented as a full browser accessibility certification. There is no TypeScript
project: ESLint, JSX compilation, runtime component tests and browser checks are the applicable checks.

## Price proposals and slide provenance

Inline `PHP`/`₱` prices on the same item/calorie line are now proposed to review alongside their
source URL. They remain **skip/no**, with a human-review warning. Multiple different prices on a
line are suspect. A price-only menu with no calories is not automatically turned into a nutrition
record; manually review it and leave unsupported fields unknown. Real Philippine price-image
extraction remains unverified without such source material.

The review CSV also includes `source_images` (semicolon-separated raw image filenames). This
preserves the contributing slide(s) through database load and API responses as an array. A caption
record has an empty image array. Old review headers remain supported. Existing verified corrections
and image references are protected against unverified updates. An updated source can be imported
under a new ID and reconciled through a new human review; do not overwrite raw evidence.

The public Jollibee USA PDF verification is isolated from the Philippine development catalog.
Its full table did not automatically parse. Manually transcribed test records stayed unverified and
were only loaded in a disposable verification database, with a non-live location fixture and an
explicit warning in the template response. This is not an Instagram retrieval or live Places test.


## Publish the approved local commit

The GitHub connector verifies dpsahagon and push access, but its write API returned 403 Resource
not accessible by integration. The local Git helper has no credential and SSH has no identities.
The work is committed locally; there is no pushed branch or draft PR yet. Authenticate as the
same account, then publish the existing commit. Do not create a fork or force-push.

```bash
brew install gh
gh auth login --hostname github.com --git-protocol https --web
gh auth switch --hostname github.com --user dpsahagon
gh api user --jq .login
gh api repos/bryanlara11/Calcipher --jq '.permissions.push'
```

Continue only when the login is `dpsahagon` and push is `true`. Decline any prompt to change a
**global Git credential helper**; the command below uses an invocation-only helper instead.
No password/token needs to be pasted into chat or committed.

```bash
cd /path/to/Calcipher
git status --short --branch
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push -u origin dpsahagon/instagram-ingestion-fix
gh pr create --repo bryanlara11/Calcipher --base main --head dpsahagon/instagram-ingestion-fix \
  --draft --title "Stabilize ingestion and refresh CalCipher branding and themes" --body-file docs/PR_BODY.md
```

The user has already approved these operations after identity/access verification. A normal
push will reject an unexpected divergent remote branch; investigate rather than forcing it.
Do not merge the draft without review and approval.

For the current authorized pilot, dataset-bound approval receipts and source evidence, see
[DATA-COLLECTION.md](DATA-COLLECTION.md).
