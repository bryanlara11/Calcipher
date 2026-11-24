# CalCipher implementation handoff — 2026-11-25

## Scope and branch

Continue on `dpsahagon/instagram-ingestion-fix` in `bryanlara11/Calcipher`. Main has not been merged.
Preserve existing work. Use Python 3.12, the backend virtual environment and Node 22 LTS with
npm 11.21.3. Follow `README.md` and `docs/OPERATIONS.md`; preserve existing `.env` credentials.
No private database, session, `.env`, raw collection cache or permission receipt belongs in Git.

## Product behavior implemented

- Home explains the workflow. About menu data is a separate page linked only from Home.
- Search finds menu items by dish/brand and optional calorie, protein or budget limits. Results
  stay on Search. Structured fields override conflicting numbers in query text.
- Nearby displays actual branches linked to active catalog brands, groups them by brand and
  offers a brand filter. Two nearest branches appear first; remaining locations and menus expand
  on demand. Brand photos are labelled and do not imply every branch is pictured.
- Meal Chat uses the same pin, radius and provider as Nearby. Earlier replies show when their
  area is stale. Pending previews are separate from reviewed recommendations.
- Favorites saves restaurants in this browser/device (`calcipher.favorites.v1`). Meal Plan saves
  dated Breakfast/Lunch/Dinner/Snack selections (`calcipher.mealPlan.v1`), removes entries and
  totals known nutrients with missing values explicitly reported. Neither feature is cloud sync.
- Settings contains theme, map connection and privacy. It does not duplicate menu-data content.
- Phone/tablet/laptop breakpoints, accessible controls and mobile bottom navigation are present.
  A live 390×844 Home screenshot showed no horizontal overflow. Final iPad/laptop visual and
  planner/favorite interaction checks were limited by intermittent browser automation timeouts;
  automated route, persistence and structural accessibility checks passed.
- The latest supplied avocado logo is used throughout, including icons. Both themes use #5B8219
  accents. Light uses white surfaces without yellow UI accents. Dark uses black, shaded panels,
  white headings and gray text. Maps keep natural provider colors.
- The supplied Approved seal is used only for reviewed meals meeting selected calorie/protein
  goals. Pending matches have a text label. No goals means no Healthy badge.
- Source menu/food photographs are attributed. Tim Ho Wan pork buns use a screenshot of a
  displayed original slide. Printed prices may be historical and are never treated as current.

## Source and data boundary

The only active nutrition account is **@anongcaloriesnito**. Source-linked secondary
transcriptions preserve the original Instagram URL and exact credit evidence. Current persistent
DB: 103 total rows, 97 active pending rows across 17 brands, six other-source KFC rows archived,
zero reviewed. Archive changes preserve source evidence and approval state. Query parsing can
recognize an archived brand to avoid substituting unrelated brands; retrieval, menus, summary
counts and branch matching exclude inactive records.

Active brands: 7-Eleven, Army Navy, Chowking, Mang Inasal, McDonald's, Jollibee, Burger King,
Popeyes, World Chicken, Greenwich, Pizza Hut, Shakey's, Wendy's, Mary Grace, Tim Ho Wan,
Marugame Udon and Good Habits Cafe. These are brands; they are not 17 individually verified
branch menus. Nutrition and mapped branches are joined by brand, with availability caveats.

Active completeness: 97 calories, three reported protein values, zero reported carbohydrate/fat
values, zero checked current PHP prices. Unknown fields remain null. Budget searches exclude
unknown prices. Source permission was attested by the operator for all described reuse; the
owner permission document was not independently inspected. Permission is not nutrition approval.
The user said they approve specific IDs but has not supplied those IDs. Do not fabricate a review
receipt or mark the dataset verified.

The distributable dataset is `data/catalog/anongcaloriesnito.pending.json`, SHA-256
`b6e07748cc88a6521622da9e59187cc1eca3fc6a7f91df855ed82f7ccc9f1659`.
Use its README to load pending records on a fresh database. The immutable 36/60-record review
exports are historical, include now-excluded source rows and must not define the current scope.
The current account review export contains only 97 active records.

## Collection evidence and limits

One bounded Instaloader attempt failed with `BadResponseException: Fetching Post metadata
failed`; anonymous Python Instagram retrieval remains unresolved. Do not claim it was repaired
or bypass authentication/rate limits. Public-browser inspection covered five original posts:
7-Eleven (complete eight-slide carousel), partial Army Navy, Mary Grace, Tim Ho Wan and Good
Habits Cafe. Secondary transcriptions supplied the bounded nutrition candidates. Not every
original slide or menu item has been personally checked.

The collector now stops outside the credited account. `app.services.source_scope` audits
candidate original URLs, retained snapshot SHA-256 and visible credit; `--apply` updates
active/archive state and rematches branches. For an existing DB, run against original local
candidate batches and snapshot roots, not the distributable JSON that omits local paths:

```sh
.venv/bin/python -m app.services.source_scope --candidates /path/to/batch/candidates.json --snapshot-root /path/to/collection-root
# inspect the proposed active/archive IDs, then apply the same arguments with --apply
```

Pass all authorized batch files together. The audit treats records absent from the allowed
batch evidence as inactive. It never changes `is_verified`. Re-audit after legacy/manual imports.

## Verification completed

309 backend tests and 52 frontend tests passed, as did frontend lint and production build.
Tests cover hard filters, pending/review separation, archived-source exclusion, snapshot credit
checks, structured Search fields, map/chat synchronization, favorites, goal badges, meal-plan
persistence/totals and route accessibility. Synthetic fixtures are tests, not collected evidence.

Live API checked: health/database/embedder ready with zero stale embeddings; catalog summary
97/17/0-reviewed/97-pending; KFC menu empty even with pending opt-in; Mary Grace six pending
items; Jollibee under500 returned five pending previews and no recommendations; budget250
returned none because prices are unknown. Google keys and LLM are not configured here. OSM
is the primary map. Public Overpass can time out; cached discovery and visible outage states
remain necessary. Optional Google needs both browser Maps and server Places keys.

## Next priorities

1. Complete remaining live phone/iPad/laptop interaction checks for Favorites and Meal Plan,
   keeping any user-saved data intact. Verify long brand/menu labels and expanded plan forms.
2. Obtain actual approved record IDs after personal source review, bound to the current dataset
   SHA. Promote only those IDs through the receipt workflow. Recheck original serving bases.
3. Collect more non-fast-food records from the same account using owner originals/exports or
   permitted bounded collection. Preserve unknown macros/prices and exact source links.
4. Verify current branch-specific prices separately if budget recommendations are needed.
5. Configure optional Google/API grants only when supplied; no keys or sessions in Git.
6. Review and merge through the normal repository workflow. Do not push directly to main or
   force-rewrite history. A PR body is prepared in `docs/PR_BODY.md`; connector PR creation was
   previously rejected with HTTP403, so no PR is claimed.
