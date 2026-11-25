# CalCipher implementation audit and handoff - 24 November 2026

This report supersedes the [23 November audit](AUDIT-2026-11-23.md), which is retained as history.
Work continues on the original repository, `bryanlara11/Calcipher`, branch
`dpsahagon/instagram-ingestion-fix`, based on `38670a5e2330cb08e5fd9e6f90679fc46f0c8625`.
All prior uncommitted ingestion improvements were retained and backed up before redesign.
The FastAPI/React/Google Places/PostgreSQL/pgvector architecture and all-distilroberta-v1 model remain.

## Completed implementation

- Reorganized the existing application into branded navigation, meal search/filter sidebar,
  restaurant map workspace, responsive assistant panel, and retained retrieval inspector.
  Added food/calorie/protein/PHP form controls that use the existing chat API contract.
- Shared cream/forest-green theme tokens style cards, forms, messages, popups, errors and
  inspector. Mustard, coral, olive and tomato are supporting accents. The original supplied
  culinary logo is optimized to 29,632-byte WebP, preserving artwork/proportions. The same cream
  disc works on light/dark surfaces; an editable 484-byte cloche/chart SVG is the simplified icon.
  An apple-touch PNG and web manifest are included. No generic replacement logo was substituted.
- Accessible Light/Dark/System selection is persisted locally and restored on refresh. System
  mode follows OS changes; a head script applies appearance before React. Storage failures
  remain usable for the visit. Mobile chat keeps an explicit accessible label; Escape closes panels.
- Preserved menus, nutrition display, map locate actions, radius controls, geolocation opt-in,
  Google attribution, existing error/retry/cooldown behavior, source links, chat and inspection.
- Retained ordered authorized local import, captions, OCR caching, review flags, all-or-nothing
  validation, transactional catalog/rematching, verified-row protection, 768-dimensional contexts,
  source-backed PHP filtering and record-ID-only LLM selection from the earlier work.
- Added inline `PHP`/`₱` price proposals on item/calorie lines. They remain skip/no and require
  source checking before a price can load. Multiple prices are suspect. Added contributing
  `source_images` to review CSV, PostgreSQL JSONB and API responses. Caption-only records retain
  an empty image array. No unverified update overwrites a checked correction or its image references.

## Dependency security

Before: npm audit reported three package entries, all development/build tooling:

| Entry | Severity | Exposure | Resolution |
| --- | --- | --- | --- |
| Vite 5.4.11 | High | Development server / related middleware; not a library shipped in the static JS bundle | 6.4.6 |
| esbuild <=0.24.2 (via Vite) | Moderate | Development server cross-origin response exposure | 0.25.12 via patched Vite |
| PostCSS 8.4.49 | High | Build-time stylesheet/source-map processing | 8.5.33 |

Advisories include [esbuild GHSA-67mh-4wv8-2f99](https://github.com/advisories/GHSA-67mh-4wv8-2f99),
[PostCSS GHSA-qx2v-qp2m-jg93](https://github.com/advisories/GHSA-qx2v-qp2m-jg93), and
[Vite GHSA-9jrq-w75r-8gcw](https://github.com/advisories/GHSA-9jrq-w75r-8gcw).
The saved audit JSON contains every applicable advisory, not just those examples.

Vite 5 had no complete fix for the current advisory ranges. Vite 6.4.6 is the smallest patched
line compatible with the existing React plugin's declared peer range. React 18.3.1, Tailwind
3.4.17 and that plugin were retained; no force upgrade was used. The added test runner was
updated to patched Vitest 4.1.12 after the final audit caught advisories in its older version.
Node 22.23.5 LTS was installed alongside Node 23 without global relinking. npm 10's optional-peer
resolver failed during setup; npm 11.21.3 generated the working lockfile without legacy-peer or
force flags. Current full and production-only audits both report **zero known findings**.
This is the audit service's result for the resolved graph, not a guarantee against future issues.

## Instagram verdict: the original collection problem is still unresolved

| Question | Status | Evidence and practical meaning |
| --- | --- | --- |
| Is original live Instagram scraping resolved? | **Not Working** as a verified operational path | No authorized live collection/session/owner API grant was available. Local import is a fallback, not a scraping repair claim. |
| Does the app automatically retrieve Instagram posts? | **Not Tested** live | Optional Instaloader code remains; 401/429/auth stops are covered by mocks. No official Meta API client has been configured. |
| Does it still need manual downloading/importing? | **Working** fallback | Owner-supplied originals/export plus a manifest and exact captions import reliably. Acquisition still needs the owner or another permitted source. |
| Does OCR work on real restaurant images? | **Partially Working** | Real public nutrition-table images yielded text/numbers, with errors. Full-table automatic parsing yielded zero items. |
| Can calories/macros be extracted reliably? | **Partially Working** | Label-style synthetic roundtrips pass. Arbitrary real tables are not reliably mapped to items/columns; manual transcription and human review remain necessary. |
| Can it extract Philippine peso prices? | **Partially Working** | Inline PHP/₱ proposals and review validation pass tests. No real Philippine price-image sample was available; general menu-price extraction is not proven. |
| Can records reach recommendations? | **Working** local integration; live discovery untested | Real import/OCR/PG/pgvector and template paths execute. A public-source assisted verification also reached a response with unverified warnings; its location was a fixture. |
| What remains for a live demonstration? | **Partially Working** prototype | App, themes, pipeline and local tests work; real permitted PH media, human-checked records, and both Google keys/billing are still needed. Gemini is optional. |

[Meta's Instagram Login API](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login)
serves professional-account workflows with authorization, not arbitrary public-account downloading.
The source account's permissions/type are unknown. Prefer owner permission/originals/exports,
then local import. Academic purpose does not grant access. No credentials, cookie extraction,
CAPTCHA bypass, proxy rotation or other evasion was added; Instaloader stops on platform refusals.

## Real-source verification (not a mocked OCR success)

Downloaded the public October 2026 US nutrition PDF linked from
[Jollibee's official nutrition page](https://www.jollibeefoods.com/nutrition).
[Source PDF](https://jollibee-prod-media.s3.us-west-2.amazonaws.com/JB_USA_Nutrition_Facts_Oct_2026_40309fabb3.pdf),
SHA-256 `d0181426c59c891a16d31d349a59575e0bbba6905076858855cf50f13e9d89ef`.
It is **US data, not Philippine nutritional evidence**, and was not loaded into the development catalog.

Rendered page 1 and ran real Tesseract: 1,376 noisy characters; zero automatic records.
Row crops recovered item names and several numeric cells, but not serving text reliably.
Two assisted transcription proposals were then imported/loaded in a separate temporary pgvector
verification database. Both stayed `verified=no`, with source URL and slide references; prices
remained null. Calorie filtering returned one proposal and the template explicitly warned that
values were unverified. Location discovery used a clearly labelled non-live fixture, and the LLM
was not called. This proves the assisted local route, not reliable table extraction, live Instagram,
US-to-PH equivalence, or independent human verification.

## Executed checks

| Command/check | Result |
| --- | --- |
| Backend `.venv/bin/python -m pytest -q -ra --junitxml=../../backend-redesign-results.xml` | **278 passed, zero failed/skipped**; original 273 preserved |
| Frontend `npm run lint` | Passed (ESLint) |
| Frontend `npm test` | **14 passed**, Vitest 4.1.12 / jsdom / React Testing Library |
| Frontend `npm run build` | Passed, Vite 6.4.6 |
| `npm audit --json` | **0 findings**, including development dependencies |
| `npm audit --omit=dev --json` | **0 production findings** |
| Real Tesseract synthetic fixtures | Single/carousel/table/missing/conflict/cache scenarios pass |
| Real PostgreSQL/pgvector tests | Load, query, constraints, idempotency, migration, transaction rollback, source update and verification protections pass |
| Production RoBERTa CLI smoke | Original model loads at 768 dimensions; real CLI + OCR + load/rerun + vector retrieval executes with synthetic fixtures |
| Public restaurant PDF/crops | Actual OCR and assisted local storage/retrieval executed; automatic table parsing failed safely as described above |
| Browser visual checks | Desktop 1280x720, tablet 768x1024, mobile 390x844; both themes, chat controls and persistence checked; no horizontal overflow at measured widths |
| Accessibility | axe structural checks in both themes; actual token pairs meet tested AA text 4.5:1/control 3:1 thresholds. jsdom cannot assess rendered color contrast; no full WCAG certification is claimed. |
| Live application | Health 200, DB connected, RoBERTa active, 0 stale embeddings; missing Google key path reports an honest unavailable state |
| `git diff --check` | Passed before publication preparation |

There is no TypeScript configuration in this JavaScript project. ESLint, JSX compilation,
component/runtime tests and browser checks were run instead of claiming a nonexistent type-check.
A new test initially left a reader transaction open, blocking a schema ALTER; that test was fixed
and the full suite rerun. Intermediate failed setup/test attempts are not reported as passing.

## Remaining limitations and setup

No owner-authorized real Instagram batch or live Google/Gemini credentials were supplied.
The persistent development catalog remains empty; tests/public-source proposals are isolated.
Unknown prices/macros stay null, missing servings stay explicitly unavailable, and missing calories
remain skipped rather than guessed. Some nutrition-only searches can show unverified candidates
with explicit warnings, preserving the prototype's existing behavior; human-checked source data is
needed for a submission dataset. Historical inferred `1 serving` rows require source re-audit.

Prices are source/item/serving evidence, not live branch guarantees. Complex table OCR still
requires review. Google result/rating/quota limits, legacy Overpass fixtures, optional pg_trgm,
per-process rate limiting, and no Alembic remain. Docker was not built or tested.

See [OPERATIONS.md](OPERATIONS.md) for setup/import/OCR/review/load/API/frontend commands,
requirements, theming and troubleshooting. Live maps require separate Places server and Maps
JavaScript browser keys, enabled APIs and billing. Human review must check real permitted PH
records before they are called verified. The optional Gemini key is not needed for template answers.

## GitHub collaboration

The connected account was verified as **dpsahagon**, profile email **dpsahagon@up.edu.ph**.
Repository metadata reports **push=true**. The collaborator-permissions endpoint itself returned
403 due to integration scope, so the repository's returned permissions were used for the role check.
No other account or Bryan credential was used. Repository-local author settings match the verified
preferred email; global Git settings were not changed. The user's 24 November request explicitly
approves committing, publishing the feature branch, and creating a draft PR after verification.

The complete diff is reviewed locally and secrets/data are excluded. Publishing uses the
GitHub connector; terminal Git credentials are not configured. The expected commit tree is checked
against the local staged tree, and exact publication/commit/PR results are recorded in the final
handoff artifacts. Main is unchanged; no force-push, unnecessary fork, or merge is authorized.


### Actual publication outcome

The first connector upload (`create_blob`) returned HTTP 403, **Resource not accessible by
integration**. This confirms a connector write-scope limitation despite the account's push role;
it does not mean dpsahagon lacks collaborator access. No remote branch or PR was created, and
no fork is needed. Terminal Git's credential helper has no dpsahagon credential and the SSH agent
has no identities. The completed tested work is committed locally under the preferred verified
email. Exact final commit SHA is in the exported handoff. Main remains at the original base.

Use the terminal authentication/push/draft-PR commands at the end of OPERATIONS.md, or reconnect
the GitHub integration with repository Contents write access. No secret/session/private source
material was committed. No force-push or merge occurred.

The final clean installation used Node 22.23.5 and the npm 11.21.3 CLI installed in local scratch
space (`node ../../npm-tooling/node_modules/npm/bin/npm-cli.js ci`, from frontend/). This changes
no global Node links or Git credential helpers. Subsequent lint/test/build and audits use the
same npm CLI. No legacy-peer or force resolver setting was required.
