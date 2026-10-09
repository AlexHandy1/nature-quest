# prototypes/

Throwaway spikes and experiments validating each piece of the Nature Quest pipeline before any of it becomes production code. Nothing in this directory should be imported by production code later — it exists to answer specific questions, cheaply, and each script's docstring states the question it was built to answer.

**Read this file before adding a new prototype or extending an existing one** — it tells you what's already been tried and the conventions to follow. For current status, open items, and decision history, see `docs/status_docs/` — this file documents what was built and why, not what state it's in today.

## Conventions (read before writing new scripts)

- **Standalone scripts.** Each prototype script does not import from another prototype script (small shared helpers like `haversine_m`, `header()`, or the GBIF fetch loop are duplicated locally instead). The one deliberate exception is `server.py`, which imports `run_pipeline` from `e2e_walk_spike_server.py` — justified there because it's a thin HTTP wrapper, not a second implementation.
- **Importing from `app/backend/services` is fine, and distinct from the rule above.** `duckdb_e2e_prototype.py` imports `anthropic_client.build_client`/`resolve_taxon_filters`, `gbif_client._select_species_across_groups`, and `waypoints.order_waypoints` directly from production — unmodified. This is a deliberate exception, not a violation of "standalone scripts": the question that prototype answers is specifically whether a redesigned data layer slots under the *existing* production logic unmodified, so importing it (rather than duplicating it) is the only way to actually test that.
- **Working scripts are checkpoints — copy, don't edit.** Once a script has been run and validated, don't modify it in place to add new functionality. Copy it to a new file first (see `e2e_walk_spike.py` vs `e2e_walk_spike_server.py`, or `narration_wikipedia_spike.py` vs `narration_eval_spike.py`) and change the copy. The original stays a stable, known-good reference.
- **Plain Messages API by default, not the Claude Agent SDK.** Every LLM call in the current pipeline is a plain `anthropic.Anthropic().messages.create()` call (tool-use for structured output where needed), not the Claude Agent SDK. This is an implementation-layer choice, not a claim about the system's overall behaviour — the dynamic, query-driven species/route/narrative generation described in the root `README.md` could be reasonably described as a nature AI agent; "not the Agent SDK" just means that behaviour is built on direct API calls rather than an agent-loop/tool-calling framework. Cost/time experiments (`species_narrative_cost_experiment1.py` vs `2.py`) showed the Agent SDK's subprocess/agent-loop overhead adds real, unnecessary cost and latency for these single-shot, no-tool-loop tasks.
- **Haiku by default.** `claude-haiku-4-5-20251001` is the default model across the current pipeline, chosen after spot-checks showed it matches Sonnet 5's output quality on these call shapes for roughly half the cost and faster wall time. Sonnet 5 remains the fallback where a quality gap shows up — e.g. as the default LLM-judge model in `narration_eval_spike.py`, deliberately a different model class than the Haiku narrator it's grading.
- **Fixed test data.** `e2e_walk_spike.py`, `e2e_walk_spike_server.py`, and `server.py`/`index.html` share the same hardcoded Retiro Park GBIF polygon/centre point so results are comparable. `narration_tts_spike.py`, `narration_wikipedia_spike.py`, and `narration_eval_spike.py` share the same fixed 5-species Retiro sample, for the same reason. Custom/user-drawn locations are prototyped separately (`e2e_walk_spike_polygon.py` / `server_polygon.py` / `web/index_polygon.html`), not as a change to the fixed-data scripts.
- **Full logging.** Every script prints prompts, raw LLM responses, token usage, and cost/timing summaries as it runs (`prototypes/logs/` holds captured runs via `... | tee prototypes/logs/<name>_$(date +%Y%m%d_%H%M%S).log`). Keep new scripts at the same logging level — it's what makes runs comparable after the fact.
- **Light TDD, scoped to deterministic logic only.** Unit tests exist only for pure, non-LLM, non-network logic (taxon resolution/validation, quota merge — see `test_intent_query_spike.py`). LLM output and rendering/geometry code are validated manually/visually, not unit tested, per this codebase's stated norm for prototypes.
- **Virtual env.** `python -m venv venv && source venv/bin/activate && pip install -r prototypes/requirements.txt`. Requires `ANTHROPIC_API_KEY` in the environment (`.env` file, loaded via `python-dotenv`) for any script that calls the Anthropic API.

## Folder map

| Folder | Contents |
|---|---|
| `scripts/` | All prototype Python scripts + their tests. |
| `reference/` | Curated GBIF API reference material (hand-written docs + verified static key caches) fed into LLM prompts. |
| `web/` | Single-page browser frontends served by the various `scripts/server*.py` apps. |
| `artifacts/` | Generated output — HTML maps/reports written by the various scripts. Regenerated on each run; not hand-maintained. |
| `ux/` | Standalone HTML UX mockups, not wired to any real data pipeline (hand-captured demo data instead). |
| `logs/` | Captured stdout from real runs (`... \| tee prototypes/logs/...log`) — the actual evidence behind decisions recorded in `docs/`. |
| `scratch/` | Local Parquet export cache for the DuckDB prototypes (`fact_sorted_by_cell.parquet`, `species_dimension.parquet`) — multi-hundred-MB, gitignored, regenerated from BigQuery on demand (`--export`), not hand-maintained. |
| `requirements.txt` | Python deps for the venv. |

## Scripts, in build order (what each answers)

### 1. Route/OSM exploration
`overpass_spike.py`, `osm_data_explorer.py`, `retiro_spike.py` — explored whether real OSM footpath routing was needed for a walk to feel useful. Superseded by the simpler "species waypoints + dashed connector" approach below — no real routing turned out to be necessary.

### 2. Waypoint ordering
`waypoint_spike.py` — validated the waypoints-and-connector approach; its nearest-neighbour ordering logic (`order_waypoints`, `haversine_m`) is reused (duplicated, per the standalone convention) throughout the rest of this directory.

### 3. Species enrichment + narrative
`species_narrative_spike.py` → `species_narrative_cost_experiment1.py` → `species_narrative_cost_experiment2.py` — moved from the Claude Agent SDK (isolated per-species agent calls with a Wikipedia tool) to plain `messages.create()` calls (Wikipedia lookup direct in Python, single batched description call, single narrative call). The plain-API shape is decisively cheaper and faster, and is the pattern every later script carries forward.

### 4. NL query → GBIF species selection
`intent_query_spike.py` (+ `test_intent_query_spike.py`, `reference/`) — the validated pattern: NL request → structured-output LLM call (forced tool-use) → per-filter taxon resolution (local cache first, then live `species/match`) → parallel `occurrence/search` per resolved filter → quota/round-robin merge across groups. Full design rationale and verified GBIF API facts are in `docs/status_docs/PLANNING_INTENT_QUERY_210726.md`.

### 5. UX exploration
`ux/map_narrative_layout_prototype.html` (+ `_v2.html`) — three switchable map/narrative UI variants tested against hand-captured demo data. Variant A ("adventure-style quest log") was chosen as the interim map style.

### 6. End-to-end integration
`e2e_walk_spike.py` / `e2e_walk_spike_server.py` — chains §2-4 into one measurable pipeline (NL query → resolve taxa → fetch/rank → order waypoints → common-name/Wikipedia enrichment → batched description → structured narrative), rendered onto the Variant A quest-log map. The `_server` copy returns a structured dict instead of just rendering, so `server.py` can serve it over HTTP.

### 7. Web frontend
`server.py` + `web/index.html` — first HTTP-serving prototype in this codebase. Minimal Flask app, one blocking `POST /run-walk` endpoint, single-page frontend (landing form → loading state → quest-log map), no build step or framework.

### 8. User-drawn polygon areas
`e2e_walk_spike_polygon.py` / `server_polygon.py` / `web/index_polygon.html` — extends the pipeline to accept an arbitrary user-drawn polygon (via Leaflet.draw) in place of the fixed Retiro geometry, including a wider GBIF year range since drawn areas won't share Retiro's observation density.

### 9. Density-cluster species markers
`e2e_walk_spike_clustering.py` — tests an adaptive density-grid cluster as a species-marker location against the plain average-of-occurrences centroid used elsewhere. Also adds a GBIF fetch scale guard (probe count, fall back to a single year above a threshold) and a retry wrapper for transient GBIF errors.

### 10. Query validation gate
`e2e_walk_spike_full_validation.py` / `server_full_validation.py` / `web/index_full_validation.html` — splits the pipeline into a cheap validation step and an expensive completion step, so the system can gate the expensive half behind an explicit user decision only in the cases where it would otherwise silently substitute a different search than what was asked for (no taxon resolved, or zero occurrences found) — cases where the same search just returns fewer/closer results proceed automatically with an explanatory note instead.

### 11. Audio narration + Wikipedia grounding
A separate track from §1-10's GBIF/waypoint pipeline, exploring the spoken/written per-waypoint narration piece described but not implemented in `app/`. Fixed 5-species Retiro Park sample throughout, isolating narrative/TTS/eval variables from GBIF/LLM query variability.
- **`narration_tts_spike.py`** — narrative (plain Haiku call, names + locations only) → text-to-speech, comparing ElevenLabs against Kokoro-82M via OpenRouter's OpenAI-compatible endpoint.
- **`narration_wikipedia_spike.py`** — text-generation only, no TTS. Fetches a Wikipedia extract per species (mirrors `app/backend/services/wikipedia_client.py`'s resolution logic, but also keeps `extract`/`url`, which production discards after using only the image) and compares a baseline narrative against a Wikipedia-grounded one.
- **`narration_eval_spike.py`** — adds an automatable eval pass on top of the above: programmatic checks (length, no time-of-day references, no TTS-unfriendly dash pauses) plus an LLM-judge check (faithfulness to the retrieved extracts, no ungrounded habitat claims), run against both narrative variants.

### 12. Local DuckDB/Parquet index — removing the live GBIF dependency
A separate track exploring whether a pre-aggregated local index, rebuilt monthly from GBIF's BigQuery public dataset, can replace the live `species/match` + `occurrence/search` calls §1-11 are all built on. Scratch BigQuery objects live in `nature-quest-504414.gbif_exploration` (30-day auto-expiry).
- **`duckdb_parquet_prototype.py`** (+ `_madrid.py`, `_spain.py` variants) — question: can a single request prune straight to the few cells covering a polygon, by querying a local `cell`-sorted Parquet export with DuckDB, without loading the whole fact table? Validated at three scales (Retiro: 1/212 row groups read; Greater Madrid: still 1/212; mainland Spain: 12/212 across two disjoint clusters, proving DuckDB skips the gap between matches, not just everything from first match to last) — row-group pruning works, including the harder non-contiguous case.
- **`duckdb_e2e_prototype.py`** / **`server_duckdb_e2e.py`** / **`web/index_duckdb_e2e.html`** — question: does the full request path (NL query → LLM taxon resolution → candidate cells → DuckDB query → selection/ordering → map) work end-to-end against this local index, reusing the existing production taxon-resolution/selection/ordering logic unmodified (see the import exception above)? Two real, evidenced taxonomy mismatches were found and fixed, not assumed away:
  - **`specieskey` values are Catalogue of Life (COL XR) taxon ids, not legacy GBIF numeric backbone keys** — e.g. *Pica pica*'s real GBIF key `2482484` comes back as `specieskey='4HPXM'` (GBIF migrated its backbone to COL XR; see https://data-blog.gbif.org/post/catalogue-of-life-taxonomic-backbone/). Doesn't break the fact/dimension join (both sides share the same source column) but does invalidate any common-names batch job or GBIF species link built on the assumption it's a numeric key — kept as an opaque string, not cast to `int`.
  - **The Testudines rank override in production's `TAXON_GUIDANCE`** (forces `class` rank, tuned for GBIF's backbone API, which has no single `Reptilia` class) **is backwards for this local table** — verified directly: `class='Reptilia'` / `order='Testudines'` here. Fixed with a narrow, evidenced translation (`adapt_filter_for_local_table()`), not a prompt rewrite.
  - Candidate-cell generation (bbox grid-sampling) is copied from `duckdb_parquet_prototype.py`, per the standalone-scripts convention — the one exception to that rule is the `app/backend/services` imports noted above, not this.
  - Common names/GBIF links and the polygon→cells geometry refinement were explicitly out of scope — species shown by scientific name only, fixed Retiro polygon only (no draw tool, already covered by §8).

See `docs/status_docs/` for the full session-by-session history, current status, and open items behind these decisions.
