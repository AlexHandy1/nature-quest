# prototypes/

Throwaway spikes and experiments validating each piece of the nature-walker pipeline before any of it becomes production code. Nothing in this directory should be imported by production code later — it exists to answer specific questions, cheaply, and each script's docstring states the question it was built to answer.

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
| `logs/` | Captured stdout from real runs (`... \| tee prototypes/logs/...log`) — the actual evidence behind decisions recorded in `planning_and_status_docs/`. |
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
- **`intent_query_spike.py`** — the current, validated pattern: NL request → LLM structured-output call (forced tool-use) → per-filter taxon resolution (local cache first, then live `GET /species/match`) → parallel `GET /occurrence/search` per resolved filter → quota/round-robin merge across groups (handles mixed-taxa requests and empty-group redistribution). Full design rationale, verified GBIF API facts, and the process issue that led to re-verifying the API live (don't trust remembered API shapes) are in `planning_and_status_docs/PLANNING_INTENT_QUERY_210726.md`.
- **`test_intent_query_spike.py`** — unit tests for the deterministic pieces of the above (cache lookup, match validation, quota merge). Run with `pytest prototypes/scripts/test_intent_query_spike.py`.
- **`reference/`** — the curated prompt material `intent_query_spike.py` (and everything downstream of it) depends on:
  - `gbif_docs_summary.md` — hand-written GBIF API summary injected into the system prompt (not a raw OpenAPI dump).
  - `gbif_kingdom_keys.json` — all 9 GBIF kingdom values, fully enumerable, hardcoded.
  - `gbif_common_class_keys.json` — common Animalia classes (birds, insects, mammals, etc.) plus the 4-class reptile union (GBIF retired the single `Reptilia` class in 2022).
  - `gbif_common_order_keys.json` — top-5-by-occurrence-count fish orders (a coverage holding solution, ~67% of fish observations, not exhaustive — open item).

### 5. UX exploration — Variant A ("adventure-style quest log") chosen
- **`ux/map_narrative_layout_prototype.html`** — three switchable map/narrative UI variants (`?variant=A|B|C`) built against hand-captured demo data (not live): **A — Quest Log (adventure-style)**, B — PokéStop (Pokemon Go), C — Inventory (Minecraft). **Variant A was chosen** (see `planning_and_status_docs/WORK_SUMMARY_210726.md`) as the interim map style while a longer-term WebGL 3D map direction is explored separately (see `FEATURE_IDEAS_BACKLOG.md`).
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

### 8. User-drawn polygon areas — prototyped, several open items remain (see `planning_and_status_docs/WORK_SUMMARY_230726.md`)
- **`e2e_walk_spike_polygon.py`** — fork of `e2e_walk_spike_server.py` (untouched). `run_pipeline()` takes `polygon_wkt` / `center_lat` / `center_lon` as parameters instead of reading hardcoded `GBIF_POLYGON`/`CENTER_LAT`/`CENTER_LON` globals, so an arbitrary user-drawn polygon can be searched instead of the fixed Retiro one. Also widens the GBIF `year` filter to a range (`YEAR_RANGE = "2023,2026"`, GBIF accepts a comma range) rather than the single `YEAR = 2026` the Retiro scripts use — user-drawn areas won't have Retiro's observation density, so a single year risks empty results. CLI-runnable standalone via `--polygon` / `--center-lat` / `--center-lon` flags, defaulting to the Retiro geometry.
- **`server_polygon.py`** — fork of `server.py`, on port **5051** (so it can run alongside `server.py`'s 5050). `/run-walk` accepts `{query, polygon}` (a list of `[lat, lon]` vertices from the browser) instead of `{query, location}`; converts to GBIF WKT (`polygon_points_to_wkt` — flips to `lon lat` order, closes the ring) and a centroid (`polygon_centroid` — simple average of vertices) before calling `e2e_walk_spike_polygon.run_pipeline()`.
- **`web/index_polygon.html`** — fork of `index.html`, adds a real Leaflet.draw (`leaflet-draw@1.0.4`) polygon tool. Five JS-swapped states: welcome/onboarding card → full-screen draw-your-area map → landing form (query + drawn-area summary) → loading → the adventure-style quest-log map (with both "New Walk" and "New Area" actions, the latter returning to the draw step).
  ```
  source venv/bin/activate
  python prototypes/scripts/server_polygon.py
  # open http://localhost:5051
  ```
- **Validated this round:** a user-drawn polygon survives the full trip — browser Leaflet coordinates → GBIF WKT geometry → real occurrence search → waypoint ordering from the drawn area's own centroid → rendered quest-log map.
- **Known issues / open items** (see `planning_and_status_docs/WORK_SUMMARY_230726.md` for full detail): the "show me something rare" test intent hangs/fails and needs investigating across all test intents; behaviour for sparse/no-observation areas isn't defined; the drawn area's boundary isn't shown once you reach the map view; onboarding/UX still needs deeper design thought; no caching yet; Retiro isn't offered as a quick default alongside draw-your-own; centroid is a naive average-of-vertices (not weighted toward where observations actually cluster).

## What's proven vs. still open

**Proven end-to-end:** NL query → real GBIF species selection (including mixed-taxa and empty-group handling) → real waypoint ordering → real GBIF/Wikipedia enrichment → real generated narrative → rendered, interactive map — triggerable from a browser, on Haiku, for a few cents per run. Also proven for an arbitrary user-drawn area, not just the fixed Retiro polygon (see §8).

**Explicitly open / not built yet:**
- Background-job + polling for real per-step loading progress in the web frontend (currently one blocking request + generic spinner).
- Fish taxon coverage is a ~67%-by-volume holding solution, not exhaustive (`reference/gbif_common_order_keys.json`).
- The longer-term WebGL 3D map direction (Variant A/Leaflet is the interim choice).
- Naming decision (Nature Walker vs. Nature Quest) — still open.
- The "show me something rare" test intent hangs/fails — needs investigation across all test intents, with a defined desired behaviour.
- Behaviour for sparse/no-observation areas (drawn or otherwise) isn't defined beyond a generic error message.
- Deeper UX design for the draw-your-own-area flow: how to introduce/explain it, showing the drawn boundary on the quest-log map, dropping the sword-emoji styling.
- Caching (not yet scoped — likely GBIF responses and/or LLM calls).
- Retiro Park as a quick default/selectable option alongside draw-your-own-area.
- A better-than-centroid method for anchoring the map/route on the densest cluster of observations, not a naive average of the drawn polygon's vertices.
- A full technical plan for the production build (testing, CI/CD, observability/monitoring) — not started.

See `planning_and_status_docs/` for the full session-by-session history behind these decisions.
