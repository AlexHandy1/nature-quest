# prototypes/

Throwaway spikes and experiments validating each piece of the Nature Quest pipeline before any of it becomes production code. Nothing in this directory should be imported by production code later — it exists to answer specific questions, cheaply, and each script's docstring states the question it was built to answer.

**Read this file before adding a new prototype or extending an existing one** — it tells you what's already been tried, what won, and the conventions to follow.

## Conventions (read before writing new scripts)

- **Standalone scripts.** Each prototype script does not import from another prototype script (small shared helpers like `haversine_m`, `header()`, or the GBIF fetch loop are duplicated locally instead). The one deliberate exception is `server.py`, which imports `run_pipeline` from `e2e_walk_spike_server.py` — justified there because it's a thin HTTP wrapper, not a second implementation.
- **Working scripts are checkpoints — copy, don't edit.** Once a script has been run and validated, don't modify it in place to add new functionality. Copy it to a new file first (see `e2e_walk_spike.py` vs `e2e_walk_spike_server.py` below) and change the copy. The original stays a stable, known-good reference.
- **Plain Messages API by default, not the Claude Agent SDK.** Every LLM call in the current pipeline is a plain `anthropic.Anthropic().messages.create()` call (tool-use for structured output where needed), not the Claude Agent SDK. This is an implementation-layer choice, not a claim about the system's overall behaviour — the dynamic, query-driven species/route/narrative generation described in the root `README.md` could be reasonably described as a nature AI agent; "not the Agent SDK" just means that behaviour is built on direct API calls rather than an agent-loop/tool-calling framework. Cost/time experiments (`species_narrative_cost_experiment1.py` vs `2.py`) showed the Agent SDK's subprocess/agent-loop overhead adds real, unnecessary cost and latency for these single-shot, no-tool-loop tasks.
- **Haiku by default.** `claude-haiku-4-5-20251001` is the default model across the current pipeline (`e2e_walk_spike.py`, `server.py`), chosen after spot-checks showed it matches Sonnet 5's output quality on these call shapes for roughly half the cost and faster wall time. Sonnet 5 remains the fallback if a future task shows a real quality gap.
- **Fixed test location: Retiro Park, Madrid.** `e2e_walk_spike.py`, `e2e_walk_spike_server.py`, and `server.py`/`index.html` all use the same hardcoded GBIF polygon (`GBIF_POLYGON`) and park-centre point (`CENTER_LAT`/`CENTER_LON`) so results are comparable across those prototypes. Custom/user-drawn locations are prototyped separately in `e2e_walk_spike_polygon.py` / `server_polygon.py` / `web/index_polygon.html` (see §8 below) — a standalone fork, per the checkpoint convention above, not a change to the fixed-Retiro scripts.
- **Full logging.** Every script prints prompts, raw LLM responses, token usage, and cost/timing summaries as it runs (`prototypes/logs/` holds captured runs via `... | tee prototypes/logs/<name>_$(date +%Y%m%d_%H%M%S).log`). Keep new scripts at the same logging level — it's what makes runs comparable after the fact.
- **Light TDD, scoped to deterministic logic only.** Unit tests exist only for pure, non-LLM, non-network logic (taxon resolution/validation, quota merge — see `test_intent_query_spike.py`). LLM output and rendering/geometry code are validated manually/visually, not unit tested, per this codebase's stated norm for prototypes.
- **Virtual env.** `python -m venv venv && source venv/bin/activate && pip install -r prototypes/requirements.txt`. Requires `ANTHROPIC_API_KEY` in the environment (`.env` file, loaded via `python-dotenv`) for any script that calls the Anthropic API.

## Folder map

| Folder | Contents |
|---|---|
| `scripts/` | All prototype Python scripts + their tests. |
| `reference/` | Curated GBIF API reference material (hand-written docs + verified static key caches) fed into LLM prompts — see below. |
| `web/` | Single-page browser frontends: `index.html` (served by `scripts/server.py`, fixed to Retiro) and `index_polygon.html` (served by `scripts/server_polygon.py`, user-drawn area). |
| `artifacts/` | Generated output — HTML maps written by the various scripts. Regenerated on each run; not hand-maintained. |
| `ux/` | Standalone HTML UX mockups, not wired to any real data pipeline (hand-captured demo data instead). |
| `logs/` | Captured stdout from real runs (`... \| tee prototypes/logs/...log`) — the actual evidence behind decisions recorded in `docs/`. |
| `requirements.txt` | Python deps for the venv. |

## Scripts, in build order (what each answers, and what won)

### 1. Route/OSM exploration — abandoned direction
- **`overpass_spike.py`** — earliest spike: can OSM's REST API return footpaths for Retiro Park? Yes, but superseded by the waypoint approach below.
- **`osm_data_explorer.py`** — explores what else OSM returns for the Retiro bbox beyond footways.
- **`retiro_spike.py`** — GBIF → species selection → OSM route → map pipeline for Retiro Park. Superseded by `waypoint_spike.py`'s simpler "waypoints + dashed connector" approach (no real OSM routing needed for the walk to feel useful).

### 2. Waypoint ordering — validated, now folded into `e2e_walk_spike.py`
- **`waypoint_spike.py`** — question: does "5 species waypoints + dashed connector" feel better than a single OSM path? Contains the nearest-neighbour ordering logic (`order_waypoints`, `haversine_m`) that `e2e_walk_spike.py` later reuses (duplicated, per the standalone convention).

### 3. Species enrichment + narrative — plain Messages API won over the Agent SDK
- **`species_narrative_spike.py`** — first version: Claude Agent SDK, isolated per-species agent calls with a Wikipedia tool, plus a narrative agent call.
- **`species_narrative_cost_experiment1.py`** — batches the 5 per-species calls into one Agent SDK session; adds `--model` for comparison.
- **`species_narrative_cost_experiment2.py`** — **the winning shape.** Drops the Agent SDK entirely: Wikipedia lookup called directly in Python, both the batched descriptions and the narrative are single plain `messages.create()` calls. Decisively cheaper/faster than experiment1 — this is the pattern `e2e_walk_spike.py` carries forward (with one change: the narrative call there asks for structured per-waypoint JSON instead of one flowing blob, to drive the quest-log UI).

### 4. NL query → GBIF species selection — validated
- **`intent_query_spike.py`** — the current, validated pattern: NL request → LLM structured-output call (forced tool-use) → per-filter taxon resolution (local cache first, then live `GET /species/match`) → parallel `GET /occurrence/search` per resolved filter → quota/round-robin merge across groups (handles mixed-taxa requests and empty-group redistribution). Full design rationale, verified GBIF API facts, and the process issue that led to re-verifying the API live (don't trust remembered API shapes) are in `docs/status_docs/PLANNING_INTENT_QUERY_210726.md`.
- **`test_intent_query_spike.py`** — unit tests for the deterministic pieces of the above (cache lookup, match validation, quota merge). Run with `pytest prototypes/scripts/test_intent_query_spike.py`.
- **`reference/`** — the curated prompt material `intent_query_spike.py` (and everything downstream of it) depends on:
  - `gbif_docs_summary.md` — hand-written GBIF API summary injected into the system prompt (not a raw OpenAPI dump).
  - `gbif_kingdom_keys.json` — all 9 GBIF kingdom values, fully enumerable, hardcoded.
  - `gbif_common_class_keys.json` — common Animalia classes (birds, insects, mammals, etc.) plus the 4-class reptile union (GBIF retired the single `Reptilia` class in 2022).
  - `gbif_common_order_keys.json` — top-5-by-occurrence-count fish orders (a coverage holding solution, ~67% of fish observations, not exhaustive — open item).

### 5. UX exploration — Variant A ("adventure-style quest log") chosen
- **`ux/map_narrative_layout_prototype.html`** — three switchable map/narrative UI variants (`?variant=A|B|C`) built against hand-captured demo data (not live): **A — Quest Log (adventure-style)**, B — PokéStop (Pokemon Go), C — Inventory (Minecraft). **Variant A was chosen** (see `docs/status_docs/WORK_SUMMARY_210726.md`) as the interim map style while a longer-term WebGL 3D map direction is explored separately (see `FEATURE_IDEAS_BACKLOG.md`).
- **`ux/map_narrative_layout_prototype_v2.html`** — a vector re-skin variant, not chosen.

### 6. End-to-end integration — validated
- **`e2e_walk_spike.py`** — chains the winning pieces from 2-4 above into one measurable pipeline: NL query → structured GBIF query → resolve taxa → parallel GBIF fetch/rank (this step already produces each species' hotspot centroid, so no separate waypoint GBIF fetch is needed) → quota/round-robin merge → nearest-neighbour waypoint ordering → GBIF common-name + Wikipedia lookups → batched per-species description call → structured intro+per-waypoint narrative call → renders onto the Variant A adventure-style quest-log map (ported with full interactivity: journal toggle, click-to-open modal, mark-discovered). All 3 LLM calls independently model-flagged (`--intent-model` / `--description-model` / `--narrative-model`), defaulting to Haiku. Validated live on the "plants" and "mixed birds/plants/mammals" test intents — see `prototypes/logs/e2e_walk_*.log`.
  ```
  source venv/bin/activate
  python prototypes/scripts/e2e_walk_spike.py "Today I want to learn about plants" \
    2>&1 | tee prototypes/logs/e2e_walk_$(date +%Y%m%d_%H%M%S).log
  ```
  Opens the generated map (`prototypes/artifacts/e2e_walk_quest_log.html`) in your browser automatically.
- **`e2e_walk_spike_server.py`** — a copy of `e2e_walk_spike.py`, kept separate so the validated CLI script above is never edited in place. Only difference: `run_pipeline()` takes an `open_browser` flag and returns a structured dict (`{species, intro, waypoints, map_path}`) instead of just the species list, so `server.py` can render its own view from the result. Runnable identically to `e2e_walk_spike.py` from the CLI (same default behaviour).

### 7. Web frontend — validated
- **`server.py`** — the first thing in this codebase that serves HTTP. Minimal Flask app: serves `web/index.html` and exposes one blocking `POST /run-walk` endpoint that calls `e2e_walk_spike_server.run_pipeline()` in-process and returns JSON. Deliberately synchronous (no background job/polling) for this round — **background-job polling for real per-step progress is a known, explicitly requested next step**, not built yet.
  ```
  source venv/bin/activate
  python prototypes/scripts/server.py
  # open http://localhost:5050
  ```
  Requires `ANTHROPIC_API_KEY` in the environment. Logs the full pipeline output (same as the CLI scripts) to whatever terminal `server.py` is running in, plus a `[server] /run-walk ...` line per request. Errors are caught and returned as JSON (surfaced inline in the frontend's landing form), not left as an unhandled Flask crash.
- **`web/index.html`** — single-page frontend, no build step, no framework. Three JS-swapped states in one page (no navigation/reload): landing form (location fixed to Retiro, a disabled "draw your own area" placeholder for future custom-polygon support, NL query textarea) → generic loading spinner with rotating quest-flavoured status text → the adventure-style quest-log map view (duplicated from `e2e_walk_spike.py`'s `generate_quest_log_map()`, fed with real data from the server response instead of pre-baked JS arrays), with a persistent query bar docked on the map view so a new walk can be requested without leaving the map.

### 8. User-drawn polygon areas — prototyped, several open items remain (see `docs/status_docs/WORK_SUMMARY_230726.md`)
- **`e2e_walk_spike_polygon.py`** — fork of `e2e_walk_spike_server.py` (untouched). `run_pipeline()` takes `polygon_wkt` / `center_lat` / `center_lon` as parameters instead of reading hardcoded `GBIF_POLYGON`/`CENTER_LAT`/`CENTER_LON` globals, so an arbitrary user-drawn polygon can be searched instead of the fixed Retiro one. Also widens the GBIF `year` filter to a range (`YEAR_RANGE = "2023,2026"`, GBIF accepts a comma range) rather than the single `YEAR = 2026` the Retiro scripts use — user-drawn areas won't have Retiro's observation density, so a single year risks empty results. CLI-runnable standalone via `--polygon` / `--center-lat` / `--center-lon` flags, defaulting to the Retiro geometry.
- **`server_polygon.py`** — fork of `server.py`, on port **5051** (so it can run alongside `server.py`'s 5050). `/run-walk` accepts `{query, polygon}` (a list of `[lat, lon]` vertices from the browser) instead of `{query, location}`; converts to GBIF WKT (`polygon_points_to_wkt` — flips to `lon lat` order, closes the ring) and a centroid (`polygon_centroid` — simple average of vertices) before calling `e2e_walk_spike_polygon.run_pipeline()`.
- **`web/index_polygon.html`** — fork of `index.html`, adds a real Leaflet.draw (`leaflet-draw@1.0.4`) polygon tool. Five JS-swapped states: welcome/onboarding card → full-screen draw-your-area map → landing form (query + drawn-area summary) → loading → the adventure-style quest-log map (with both "New Walk" and "New Area" actions, the latter returning to the draw step).
  ```
  source venv/bin/activate
  python prototypes/scripts/server_polygon.py
  # open http://localhost:5051
  ```
- **Validated this round:** a user-drawn polygon survives the full trip — browser Leaflet coordinates → GBIF WKT geometry → real occurrence search → waypoint ordering from the drawn area's own centroid → rendered quest-log map.
- **Known issues / open items** (see `docs/status_docs/WORK_SUMMARY_230726.md` for full detail): the "show me something rare" test intent hangs/fails and needs investigating across all test intents; behaviour for sparse/no-observation areas isn't defined; the drawn area's boundary isn't shown once you reach the map view; onboarding/UX still needs deeper design thought; no caching yet; Retiro isn't offered as a quick default alongside draw-your-own; centroid is a naive average-of-vertices (not weighted toward where observations actually cluster).

### 9. Density-cluster species markers — prototyped, real trade-offs surfaced (see `docs/status_docs/WORK_SUMMARY_250726.md`)
- **`e2e_walk_spike_clustering.py`** — fork of `e2e_walk_spike_polygon.py`. Tests whether an adaptive N×N density-grid cluster (`cluster_species_hotspot()`) is a better species-marker location than the plain average-of-all-occurrences centroid used elsewhere (`rank_species()`'s `hotspot_lat`/`hotspot_lon`). Strips the per-species description and narrative LLM calls (out of scope, costly to rerun); keeps NL-query species selection and polygon-area support. Renders a comparison map: old (average) vs. new (density-cluster) marker per species, click-to-reveal raw occurrence points and the winning grid cell's own bounds. Also adds a scale guard to `fetch_gbif_occurrences()` (probe count first, fall back from the full `YEAR_RANGE` to a single year above 1000 occurrences) and a retry wrapper for transient bad GBIF responses — not yet backported to the other scripts sharing this fetch logic.
  ```
  source venv/bin/activate
  python prototypes/scripts/e2e_walk_spike_clustering.py "Today I want to learn about plants" \
    --polygon-file prototypes/reference/rascafria_area.geojson \
    2>&1 | tee prototypes/logs/e2e_walk_clustering_$(date +%Y%m%d_%H%M%S).log
  ```
  `--polygon-file` omitted defaults to Retiro. No unit tests (explicit call for this prototype — throwaway, not the usual light-TDD norm).
- **Validated this round:** clustering meaningfully diverges from the plain average whenever a species has enough occurrences (confirmed on Retiro plants and Rascafría birds, 55-332m and 111-261m divergence respectively).
- **New problem found, not solved:** GBIF data itself can contain many exact-duplicate/low-precision coordinates for a single recording location shared across species (confirmed at Rascafría — several species' clusters collapsed onto one identical coordinate, stacking markers illegibly). This is a real data artifact, not a clustering bug, but the pipeline has no handling for it — see open items below.
- **Also confirmed:** the previously-flagged "show me something rare" hang (§8 below, `WORK_SUMMARY_230726.md`) was very likely the same GBIF fetch-scaling issue found here — that query resolves to no taxon filter, which returns tens of thousands of unfiltered occurrences without the scale guard.

### 10. Query validation gate — pipeline split into two endpoints, validated
- **`e2e_walk_spike_full_validation.py`** — brings together `e2e_walk_spike_clustering.py` (density-cluster hotspots, polygon-draw support, scale guard/retry) and `e2e_walk_spike_server.py` (common-name/Wikipedia enrichment, batched description, structured narrative, quest-log map render), then splits the combined pipeline into two functions instead of one:
  - `resolve_species_query()` — STEPS 1-5 (NL query → intent → resolve taxa → fetch/rank with density-cluster hotspots → nearest-neighbour waypoint ordering). Cheap: one LLM call + free GBIF calls. Returns a validation verdict (`status`/`message`/`species`/`notes`) alongside whatever species it found.
  - `run_pipeline()` — STEPS 6-8 (common name, Wikipedia, batched description, narrative, quest-log map render), given an already-resolved species list. Expensive: 2 more LLM calls, Wikipedia lookups.
- **Question answered:** given the real pipeline's actual failure/edge shapes, does gating the expensive half behind an explicit user decision (for genuine ambiguity) or an automatic explanatory note (for cosmetic quirks) stop the system silently substituting a different search than what the user asked for?
- **Validation checkpoints, decided this session** (all computed from data STEPS 1-5 already produce, no extra GBIF calls needed):
  - **Case 1** — no taxon filters produced at all, or every filter given failed `species/match` (e.g. "show me something colourful", or a typo'd taxon) → `needs_clarification`, detected right after STEP 2, **before any GBIF fetch**.
  - **Case 4** — taxon filter(s) resolved fine, but zero occurrences found in this specific area → `needs_clarification`, detected after STEP 4.
  - **Case 2** — 1-4 species found (short of the target 5) → `ok` + a note. Proceeds automatically: the search that ran is exactly what was asked for, just with fewer results — nothing substituted.
  - **Case 3** — two or more selected species' hotspots sit within `MIN_WAYPOINT_SPACING_M` (20m — chosen to catch genuine GBIF data artifacts like a shared recording station, without flagging normal park-scale separation; real Retiro/Rascafría data showed valid gaps as low as 55m and true collisions at 0m) → `ok` + a note. Auto-resolved: same species either way, just an explanatory note, no merge/rendering change implemented yet (open item below).
  - **The governing principle:** the *only* thing that blocks is about to substitute the unfiltered `most_observed` default in place of what the user actually asked for, without saying so (cases 1/4). Everything else still runs the same search the user asked for, so it proceeds with an explanation rather than a prompt.
- **"Rare" query handling removed, not fixed:** `sort: "rarest"` was previously a schema field the LLM could set, ranking species ascending by count (surfacing singleton/1-observation records — the messy behaviour flagged in `WORK_SUMMARY_250726.md`). Removed entirely from `QUERY_SCHEMA_TOOL` (not just unused — the LLM literally cannot produce it, since tool-use schemas are enforced) and from `rank_species()`. "Show me something rare" now gets identical treatment to any other non-taxon-mappable request (case 1) rather than special rarity ranking. Revisit only once there's a real design grounded in actual GBIF metadata (e.g. date-based signals) — explicitly deferred, not solved.
- **Raw occurrence points restored:** each species dict keeps its full `occurrence_points` list (not just the cluster centroid), carried through the stateless client round-trip and rendered as small circle markers (bold, saturated per-species colours — muted/thematic colours were tried first and found illegible against the sepia-filtered map tiles), toggled on/off when that species' marker is clicked. Same feature as `e2e_walk_spike_clustering.py`'s click-to-reveal, ported into the full validation flow.
- **`server_full_validation.py`** — fork of `server_polygon.py`, port 5052. Two endpoints instead of one: `POST /gbif-species-query` (STEPS 1-5 only) and `POST /run-walk` (STEPS 6-8 only, given an already-resolved species list). **Stateless by design** — the species list is round-tripped through the client between the two calls rather than cached server-side, since no server-side session state exists anywhere else in this codebase yet and this prototype isn't the place to introduce it (see the shareable-walk-link idea logged in `FEATURE_IDEAS_BACKLOG.md`, a legitimate *future* reason to revisit that trade-off).
- **`web/index_full_validation.html`** — fork of `index_polygon.html`. Adds one new state to the flow: the **needs_clarification card**, shown only for case 1/4, with two buttons — "Try a different query" (client-side only, no server call) and "Show most-observed instead" (re-calls `/gbif-species-query` with `override: true`, which skips STEP 1 and forces the unfiltered default search). `/run-walk` is only ever reached after either the user's original query submission (when nothing needed clarifying) or this explicit override click — never as a silent automatic fallback. Case 2/3 notes render as a banner inside the map view itself.
  ```
  source venv/bin/activate && python prototypes/scripts/server_full_validation.py
  # open http://localhost:5052
  ```
- **Validated this round (CLI + live server + browser):** case 1 stops before any GBIF call with the right message; the override path completes a full real walk; a genuinely sparse/overlapping query ("I want to see reptiles" near Retiro) proceeds automatically with only the case-3 overlap note (an earlier version also surfaced GBIF sub-taxonomy detail like "class/Crocodylia" in the note — removed, since target users have no concept of GBIF classification and that detail didn't change the outcome); "show me something rare" now hits case 1 identically to "show me something colourful".
- **Known bug found and fixed this session:** the raw-points feature was initially added only to the CLI script's own `generate_quest_log_map()` — the actual browser flow renders through `index_full_validation.html`'s separately-duplicated `renderMapView()` (per this codebase's "prototypes stay standalone" convention), which hadn't been touched, and `/run-walk`'s response payload didn't carry `points` at all. Fixed by updating both the server response shape and the frontend's own render/toggle logic.
- **Explicitly deferred, not built this round:** actually merging overlapping waypoints into one shared stop on the map (case 3 currently only detects + annotates, doesn't change rendering); smaller waypoint markers; fading non-selected waypoints when one is selected; more on-map labelling explaining what the raw-observation dots mean.

## What's proven vs. still open

**Proven end-to-end:** NL query → real GBIF species selection (including mixed-taxa and empty-group handling) → real waypoint ordering → real GBIF/Wikipedia enrichment → real generated narrative → rendered, interactive map — triggerable from a browser, on Haiku, for a few cents per run. Also proven for an arbitrary user-drawn area, not just the fixed Retiro polygon (see §8).

**Explicitly open / not built yet:**
- Background-job + polling for real per-step loading progress in the web frontend (currently one blocking request + generic spinner).
- Fish taxon coverage is a ~67%-by-volume holding solution, not exhaustive (`reference/gbif_common_order_keys.json`).
- The longer-term WebGL 3D map direction (Variant A/Leaflet is the interim choice).
- Naming decision (Nature Walker vs. Nature Quest) — still open.
- The "show me something rare" test intent hang is very likely explained by the GBIF fetch-scaling issue below (root-caused in §9) — not yet backported/re-verified against the actual polygon/server scripts where it was first seen.
- **GBIF fetch scaling**: `fetch_gbif_occurrences()`'s live pagination (300/page) doesn't scale for common taxa in dense areas — a single unfiltered or common-class query can mean tens of thousands of occurrences and hundreds of sequential page requests. §9's count-then-fallback-year guard is a prototype-only stopgap, not a production design (candidates: GBIF's AWS-hosted snapshot/cache, smarter pagination — see `FEATURE_IDEAS_BACKLOG.md`).
- Behaviour for sparse/no-observation areas (drawn or otherwise) isn't defined beyond a generic error message.
- Deeper UX design for the draw-your-own-area flow: how to introduce/explain it, showing the drawn boundary on the quest-log map, dropping the sword-emoji styling.
- Caching (not yet scoped — likely GBIF responses and/or LLM calls).
- Retiro Park as a quick default/selectable option alongside draw-your-own-area.
- Density-cluster species markers (§9) work when a species has enough occurrences. The shared/duplicate-coordinate problem it surfaced now has a detection + explanatory-note answer (§10, case 3), but the actual map fix — merging overlapping markers into one shared stop, rather than just annotating that they overlap — is still open.
- "Rare" query handling: resolved for now, not by building the feature but by explicitly removing it (§10) — "show me something rare" gets the same case-1 clarification treatment as any other non-taxon-mappable request, rather than the old singleton-surfacing `sort: rarest` behaviour. A real design (grounded in actual GBIF metadata, e.g. date-based signals) is still open, deliberately deferred.
- Sparse/no-observation area UX (§8's open item) now has a real answer for the two "would silently substitute a different search" cases (§10, case 1/4 — a `needs_clarification` gate) and the two "same search, just fewer/closer results" cases (§10, case 2/3 — an automatic note). Still open: the case-3 map-rendering fix itself (see above), and this validation gate hasn't yet been extended to the fixed-Retiro (`server.py`) or original polygon (`server_polygon.py`) flows — only `server_full_validation.py`.
- A full technical plan for the production build (testing, CI/CD, observability/monitoring) — not started.

See `docs/` for the full session-by-session history behind these decisions.
