# CalCipher handoff — 2026-11-25

Continue on `dpsahagon/instagram-ingestion-fix` in `bryanlara11/Calcipher`. Main is unchanged.
Use Python 3.12, the backend virtual environment, Node 22 LTS and npm 11.21.3. Preserve `.env`.
Setup and operational commands are in `README.md` and `docs/OPERATIONS.md`.

## Current product behavior

Home has a short, plain guide. About menu data is linked only from Home. Search finds meals and
also offers a collapsed all-brand menu browser. Nearby is for real branch locations: search by
restaurant/brand name, filter category and sort A–Z, Z–A or by distance. The list initially shows
three brand cards with one nearest branch each; Show more reveals three more. Map popups begin with the restaurant name,
distance and a View menu button. Menus expand inside a bounded popup, with resize handling and
theme styles that outrank Leaflet's late-loaded white defaults. Map errors have a local fallback.

The latest interface uses a compact top header, with a visible Settings label and five primary
links (a bottom bar on phones). The logo opens Home. The sidebar and timed splash are gone.
Home uses a 687px Crisostomo source photo and collapsed guides. Restaurant brands appear in a
plain list with three initial entries, without monogram placeholders or repeated nested cards.
Category/sort controls live in Area & filters; Fast food & pizza is one category. Long names and source
details wrap inside cards; expanded menus in Search, Nearby lists and Favorites use the page's
scroll instead of cutting through cards in nested scroll boxes. Phone layouts give the restaurant
count its own row, stack narrow selectors and show meal-plan macro totals as readable rows.
Map popups keep meal names first; photos stay in the full menu views, and zoom controls do not
overlap an open popup. The original avocado color, dark appearance and saved meals are preserved.

Source images keep their aspect ratio in bounded previews and offer full-image/source links.
The small skillet image was replaced on Home by a larger original. No generated food pictures
or synthetic sharpening was used; source resolution remains the limit.

Chat recognizes heavy/filling and light snacks. Heavy-snack ranking prefers portions around
300–600 kcal as a soft preference, while all explicit limits stay hard. Named rice/drink bundles
are excluded from snack requests. This is a browsing heuristic, not a nutrition prescription or
approval. Light snacks sort by calories ascending. Exact food searches still reject unrelated
matches. Brand aliases are stripped before food matching.

Explicit calorie/protein/macro/price questions use active catalog records without requiring a
mapped branch. `catalog_answers` contains CatalogItem objects without invented location fields.
MenuRecords renders their details and source links. Missing protein/prices are answered plainly.
Short follow-ups can inherit the previous successful request's food and limits, within the same
area/provider. New dishes start fresh; Search requests stay independent. This is bounded menu
conversation, not a general assistant or an ingredient/allergen verification system.

Meal Chat and meal Search show restaurant names first, with details collapsed until expanded.
Explicit food terms are filtered before vector ranking; banana no longer returns unrelated food.
A food absent from the active catalog returns a clear no-match reply before location discovery.
Exact pending matches are shown before widening a search for reviewed recommendations.
Calories, protein, budget and distance limits remain hard constraints. Missing values stay null.
Retrieval details expand in the Chat page's normal flow and scroll within their own panel.

Settings has appearance, interactive-map and device-location controls, connection status and a
plain explanation of stored data. Turning maps off unmounts the renderer and uses restaurant
lists. Disabling location requests disables the location button. Browser permission is still
required when the user explicitly requests location. Map/location preferences persist locally.

Meal Plan supports daily and weekly views, a date picker, user-entered daily calorie targets,
progress and known macro totals. Week navigation shows seven selectable dates. Month is disabled
and labelled Coming soon. No calorie target is prescribed automatically. Favorites, plans and
targets remain browser-local. Existing user-saved meals were preserved during verification.

## Community badge — latest user decision

The badge now uses **actual healthy-choice ratings**, replacing the earlier goal/system-match
rule. A Community pick needs at least three ratings with an average of four stars or higher.
Meals with no qualifying ratings get no badge. Home displays a clearly described example seal.
Rating controls accept one through five stars; another vote from the same browser identity
updates its existing vote. No sample votes were inserted into the live catalog.

`meal_ratings` stores item ID, SHA-256 of an anonymous browser UUID, score and update time.
Votes never set `is_verified`. Their summary is attached to menu/chat responses; the client
shares updated summaries through RatingsContext. POST `/api/v1/meal-ratings/{item_id}` validates
scores and active items, with the existing request rate limiter. Browser identities are not
verified user accounts; add authenticated identities/moderation before treating rankings as
strong public reputation signals. A community opinion does not verify a calorie source.

## Catalog, sources and images

121 active pending records across 21 brands; six separate-source KFC rows remain archived.
There are zero human-approved records and zero checked current PHP prices. Known fields:
121 calories, 11 protein, eight carbohydrate and eight fat values. Unknowns are not zero.

Latest batch: Crisostomo (8), CoCo Ichibanya (6), Dot Coffee (6), Conti's (4). Each brand was
first observed in @anongcaloriesnito's public posts. Nutrition and images were then researched
separately, as explicitly requested. `source_post_url` identifies the Instagram brand origin;
`nutrition_source_url` identifies external calorie evidence. Both links appear in the interface.

Eight Crisostomo entries were matched to rendered labels in the official Florabel group PDF.
The other 16 new values are labelled community transcriptions from explicit Philippine item/
variant pages. CoCo Japan figures were not substituted; rice weight/inclusion remains unknown.
Conti's pastries are per piece, including Lemon Squares; box prices were not made into meal
prices. Dot drink sizes/temperatures are separate. Conflicting snack summaries were excluded.
Food images link to their original sources and illustrate dishes/brands, not every branch.

Dataset: `data/catalog/anongcaloriesnito.pending.json`
SHA-256: `0b88e435ed5f75bd5f6ba6239cefaf56f414fb54cacfe0a9b8153f52580981be`
The 24-item subset and four new brand-origin observations are in that same folder. Its README
has fresh-path prepare/dry-run/load commands. Older exports and hashes remain historical.

`source_scope` validates retained nutrition snapshots plus matching Instagram origin observations
(or older explicit-credit snapshots), and rematches real branches. Include all five original
batches in a scope audit. Published JSON omits local evidence paths; use retained local batches
for re-audits. The bounded Instaloader attempt still failed; automated Instagram retrieval has
not been demonstrated. Nine original posts have been inspected across the project, not every
item/slide. No specific human-approved IDs have been supplied; do not invent a review receipt.

## Verification and remaining work

329 backend tests and 62 frontend tests cover source scope, exact/relevant retrieval, review
separation, anonymous rating uniqueness/thresholds, favorites, calendar/target persistence,
settings opt-outs, three-brand lists and accessibility. Lint and production build pass.
Live API checks: 121 records/21 brands, banana returns no matches, Jollibee under500 returns
five pending matches and zero recommendations, and all added menus retain separate evidence.
Browser checks cover compact/expanded map popups, theme contrast, responsive containment,
restaurant filters, Chat disclosures, inline inspector, calendar and Settings controls.

Google remains optional and was not exercised without keys. OSM is the current provider;
public Overpass services can time out, so cached discovery and visible outage states remain.
A brand can have menu data without an OSM branch; Browse all brands keeps its menu accessible.
The methodology link is an existing external worththecals page credited to Felix Macaspac.
It does not disclose whether AI helped author it; no human-only authorship claim is made.

Next: obtain real selected-ID source review; extend same-account brand discovery and checked PH
nutrition; verify branch prices separately; finish account-level rating safeguards if deploying
publicly. Review and merge normally—do not push to main or force-rewrite history. PR text is in
`docs/PR_BODY.md`; no PR is claimed (earlier connector creation returned HTTP403).
