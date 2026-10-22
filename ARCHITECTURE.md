# Architecture

A current, whole-system map of what's built and how it works today — not a roadmap. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file. Product scope, phasing, and what's planned or deferred live in `docs/prds/`, `docs/specs/`, and `docs/status_docs/`, not here.

## Overview

Nature Quest is a FastAPI backend + Vite/React frontend that turns a natural-language request and a drawn search area into a nature walk grounded in real GBIF-derived species-occurrence data. `MapView` is the whole product surface: a user draws or accepts a default search area, submits free text, and gets back a route-ordered species list, each enriched with a common name and a photo.

Request path: free text → LLM taxon resolution (OpenRouter, model-agnostic client — currently `openai/gpt-oss-120b:nitro`, generic/unpinned routing, chosen for latency — see `docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md`) → per-taxon DuckDB query against a local Parquet index (occurrence counts + common names, pre-aggregated from GBIF's public BigQuery dataset — see ADR-017) → nearest-neighbour route ordering → species enrichment (Wikipedia image/extract only; common name is already joined) → response rendered as map markers (drawn as each species' hotspot-cell footprint, not a precise point — several species can share a cell) plus a results panel. GBIF itself is **not** a live dependency of this path any more — it's a batch data source, not a per-request API call. This sits behind per-IP rate limiting and a daily LLM-call budget guardrail, with structured operational logging and consent-gated PostHog observability (client- and server-side).

Audio narration (spoken per-waypoint field-guide text) is a secondary, optional action available once a walk has resolved: a "Create narrative" control inline in the results panel triggers `POST /api/narrate`, which grounds a short narrative in each species' Wikipedia extract, generates it (Anthropic), synthesizes it to audio (OpenRouter/Kokoro-82M), and returns both in one response. See ADR-014 for the data-flow and response-shape reasoning.

## Components

```
app/backend/    FastAPI — API + serves the built frontend as static files
app/frontend/   Vite + React + TypeScript — landing page, query box, consent banner
infra/          Terraform — GCP infrastructure (Cloud Run, Artifact Registry, Secret Manager, IAM, WIF)
.github/        GitHub Actions CI/CD
prototypes/     Throwaway validation code — untouched, never deployed, not part of this architecture
```

### Backend (`app/backend/`)

```
main.py                    create_app(): FastAPI instance, JSON stdout logging config,
                             slowapi rate-limiter wiring, .env loading (load_dotenv), static-file
                             mount. warm_caches() + a lifespan hook (K_SERVICE only, no-op locally)
                             eagerly build species_index's shared DuckDB/GCS connection and both
                             consent-state OpenRouter clients before uvicorn binds the port, so
                             Cloud Run's startup probe (and therefore real traffic) waits for this
                             instead of a user's first request paying it — see
                             docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md
routers/health.py          GET /health
routers/query.py           POST /api/query — rate-limited; validate → daily budget → LLM resolve
                             to a list of taxon filters (+ token usage capture) → candidate_cells
                             (polygon → geohash cells, once per request) → one DuckDB query per
                             filter (query_species_for_filter, services/species_index.py) → quota
                             merge across filters (_select_species_across_groups) → route order →
                             Wikipedia enrichment → 3-outcome response (resolved/unresolved/
                             no_results — gbif_unavailable no longer exists, see ADR-017),
                             structured log line on every branch (REQ-017).
                             FACT_PARQUET_PATH/DIMENSION_PARQUET_PATH are required env vars (no
                             hardcoded default) read at module import time — set to the real
                             gs:// bucket on Cloud Run (infra/cloud_run.tf), to a local file path
                             in dev's .env, so dev can never accidentally point at production data
routers/narration.py       POST /api/narrate — rate-limited (same mechanism as /api/query, separate
                             daily budget, see ADR-014); validate (exactly TOP_SPECIES_COUNT species,
                             bounded field lengths) → daily budget → generate narrative (consent-gated
                             observed/plain Anthropic client, same pattern as query.py's
                             _resolve_taxon_filters) → refusal check (declined outcome, no TTS call)
                             → TTS synthesis (tts_unavailable on provider failure) → {narrative,
                             audio: base64} response, structured log line on every branch
models/query.py            QueryRequest (Pydantic) — query, distinctId, consent (default False),
                             polygon (required WKT string, no backend default — REQ-012). A
                             field_validator enforces a minimum vertex count and a maximum bounding
                             area, reusing services.species_index.parse_polygon_vertices rather
                             than duplicating WKT-parsing logic. The area cap's rationale changed
                             with ADR-017: it now bounds services/species_index.py's own candidate-
                             cell grid-sampling cost, not GBIF's rate limits — see the constant's
                             surrounding comment for the current value (not restated here, it's a
                             live guardrail, not historical context)
models/narration.py         NarrateRequest (Pydantic) — species (exactly TOP_SPECIES_COUNT
                             SpeciesInput entries, each field length-bounded), distinctId, consent
services/
  logging_client.py        log_query_outcome() plus per-pipeline-stage log lines (log_query_submitted
                             — now also takes the submitted polygon, REQ-015 — log_llm_taxon_filters_resolved,
                             log_species_selected, log_waypoints_ordered, log_species_enriched) —
                             structured Cloud Logging writes (JsonLogFormatter, main.py), all stamped
                             with distinct_id so they can be cross-referenced with PostHog's Activity view
  prompts/taxon_resolution.py TAXON_GUIDANCE + QUERY_SCHEMA_TOOL (tool name
                             produce_gbif_taxonomy_query — renamed from produce_gbif_query since
                             the output no longer shapes a literal GBIF API call, ADR-017) — the
                             single source of truth, imported by both anthropic_client.py and
                             openrouter_taxon_client.py rather than one owning it and the other
                             borrowing. System prompt directly teaches two multi-entry lay-term
                             expansions (fish, reptiles) — see ADR-011 — plus a rule forcing
                             Testudines to order rank: the opposite of the pre-ADR-017 rule
                             (class rank), which existed to work around GBIF's backbone having no
                             single Reptilia class — this app's local dimension table is the
                             opposite (Reptilia is a real class value, Testudines a real order
                             value), verified directly against the data
  prompts/narration.py      REFUSAL_SENTINEL/REFUSAL_GUIDANCE/LOCATION_GUIDANCE/
                             GROUNDED_FACTS_GUIDANCE + build_narrative_prompt(species_list) — the
                             narration prompt content; services/narration.py keeps the
                             LLM-calling logic
  secret_manager_client.py  fetch_secret(secret_id) — the one place Secret Manager access lives;
                             used by anthropic_client.py (fetched fresh per call), tts.py, and
                             species_index.py (the GCS HMAC key, see ADR-018) rather than each
                             duplicating the fetch logic. tts.py and species_index.py each cache
                             their fetched value once per process (see their own entries below) -
                             anthropic_client.py's key is deliberately not cached here, since it's
                             only the manual rollback path for taxon resolution, not the live one
  anthropic_client.py      resolve_taxon_filters() (accepts an optional on_response callback +
                             **extra_kwargs passthrough) — returns a list of {taxonRank,
                             taxonValue}, empty if no signal. Retained, unused by routers/query.py,
                             as narration.py's own Anthropic call site plus a manual, tested
                             rollback path for taxon resolution (swap the constant/import back —
                             see the OpenRouter spec). resolve_api_key() (Secret Manager on Cloud
                             Run via K_SERVICE check, local ANTHROPIC_API_KEY env var otherwise)
  openrouter_taxon_client.py Model-agnostic taxon-resolution client used by routers/query.py today —
                             MODEL constant (openai/gpt-oss-120b:nitro, generic/unpinned routing —
                             the only place a model name appears; swapping providers/models is a
                             one-constant change plus re-running the taxon-resolution/full-pipeline
                             eval suites, no other code changes), QUERY_SCHEMA_TOOL_OPENAI (built
                             once at import from
                             prompts/taxon_resolution.py's QUERY_SCHEMA_TOOL, OpenAI tool-calling
                             shape), resolve_taxon_filters() — same signature as
                             anthropic_client.py's function, calls client.chat.completions.create()
                             with forced tool choice, parses taxonFilters out of the tool-call JSON,
                             raises rather than silently returning [] on a missing/malformed tool
                             call. No explicit `provider` field is set (deliberately generic/
                             unpinned, not locked to one provider) — the `:nitro` model-id suffix
                             is what actually overrides OpenRouter's routing here, toward its
                             lowest-latency providers for this model, replacing the plain default
                             "Balanced" mode ADR-015 originally found picking backend/tier per call
                             (that finding, including its ~2x cost surprise, was specific to the
                             earlier google/gemini-3.7-flash choice — see ADR-015 for the historical
                             reasoning; the latency rationale for the current model is in
                             docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md
                             and prototypes/scripts/latency_provider_comparison.py).
  ai_observability.py      build_client(consent, distinct_id, api_key) — returns a plain
                             anthropic.Anthropic when consent=False, or a posthog.ai.anthropic.Anthropic
                             wrapper (per-call posthog_distinct_id, full $ai_input/$ai_output_choices
                             capture) when consent=True; still used by narration.py's Anthropic call
                             site. build_openrouter_client(consent, distinct_id, api_key) — same
                             consent-gating shape, pointed at OpenRouter (base_url=
                             https://openrouter.ai/api/v1): a plain openai.OpenAI when consent=False, or
                             posthog.ai.openai.OpenAI when consent=True — used by routers/query.py's
                             taxon resolution. Both branches share one lazily-built singleton PostHog
                             client from POSTHOG_PROJECT_TOKEN/POSTHOG_HOST env vars. The built
                             OpenRouter client itself is also now cached, one per consent state
                             (module-level, built at most once per process, since distinct_id is
                             passed per-call to chat.completions.create() rather than baked into
                             construction) - not rebuilt on every call the way it used to be; both
                             are warmed eagerly by main.py's startup hook on Cloud Run. Note:
                             posthog.ai.openai.OpenAI always stamps $ai_provider: "openai" on captured
                             events regardless of base_url — filter/group PostHog insights for this call
                             site by $ai_model or $ai_base_url instead. See ADR-009.
  species_index.py          The local DuckDB/Parquet query layer that replaced live GBIF calls
                             entirely (ADR-017). candidate_cells(polygon) — bbox grid-sampling to
                             geohash-6 cells (bbox only, not exact polygon containment — a known,
                             carried-forward limitation), once per request. open_index_connection()
                             — plain local connection in dev; on Cloud Run, configures httpfs with
                             the HMAC credential from Secret Manager (ADR-018) - this full build
                             (connect + httpfs + two secret fetches + CREATE SECRET) now happens at
                             most once per process. get_shared_connection() builds it lazily (with
                             a lock) the first time it's needed and caches the result;
                             get_connection_cursor() is what request handlers actually call -
                             returns a cheap per-request cursor() on that shared connection rather
                             than the connection itself, since a single DuckDBPyConnection silently
                             returns the wrong result under concurrent requests (confirmed
                             empirically, not just asserted - see the report below); the cursor
                             shares the already-loaded httpfs extension and already-created secret
                             but keeps its own independent execute/fetch state. reset_shared_connection()
                             exists for test isolation. routers/query.py calls get_connection_cursor(),
                             never open_index_connection() or get_shared_connection() directly. See
                             docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md for
                             the measured impact (t_query_execute fell ~4-5x once connection reuse
                             landed) and the concurrency-safety reasoning/evidence.
                             query_species_for_filter(
                             con, fact_path, dim_path, taxon_filter, cells) — the atomic unit: one
                             DuckDB query per taxon filter, filtering by the taxon-rank column,
                             joining common_name from the dimension table, picking each species'
                             highest-occurrence cell as its hotspot (decoded via pygeohash into
                             hotspot_lat/hotspot_lon plus the cell's real footprint,
                             cell_lat_err/cell_lon_err), returning both that cell's own count
                             (hotspot_count) and the area-wide total (count) separately — the
                             results panel shows both, since they can differ substantially.
                             parse_polygon_vertices/polygon_centroid (shared WKT parsing, used by
                             both this module and models/query.py's validator) and
                             _select_species_across_groups (quota/round-robin merge across taxon
                             filters, see ADR-011) moved here from the deleted gbif_client.py —
                             pure, data-source-agnostic, unchanged behaviour
  wikipedia_client.py       fetch_species_summary(common_name, scientific_name) — Wikipedia summary
                             API, common name first then scientific name fallback on a missing/
                             disambiguation article; returns {image_url, extract} in one fetch (the
                             extract feeds narration grounding, ADR-014). See ADR-013 for why
                             Wikipedia over GBIF's own occurrence photos. USER_AGENT includes
                             contact info per Wikimedia's policy — a bare "name/version" with none
                             gets 403'd by their robot policy (hit this in production). Trusts both
                             upload.wikimedia.org and thumb.wikimedia.org for image hosts — the
                             REST summary API actually serves thumbnails from the latter. Article
                             titles are fully percent-encoded (quote(title, safe='')) so a title
                             containing "/" (e.g. "Feral Pigeon/Rock Dove") can't be split into
                             extra URL path segments
  species_enrichment.py     enrich_species(species_list) — adds image_url/extract (Wikipedia,
                             keyed off common_name already joined by species_index.py — no longer
                             looked up here) to each of the final selected species (not every
                             candidate scanned during ranking), concurrently across species
                             (capped at 3, same pattern as elsewhere). A common name match can
                             occasionally be a real article for a slightly different species
                             (e.g. a taxonomic-split cousin) — accepted slippage for this general-
                             narrative use case, not corrected
  narration.py              generate_narrative(species_list, client, on_response=None,
                             **extra_kwargs) — grounds a short narrative in each species' Wikipedia
                             extract (GROUNDED_FACTS_GUIDANCE), includes a content-safety refusal path
                             (returns None on refusal — see ADR-014), sanitizes TTS-unfriendly dash
                             pauses deterministically after generation
  tts.py                    synthesize_speech(text, api_key, voice) — OpenRouter/Kokoro-82M
                             text-to-speech, mp3 response_format. resolve_api_key() fetches
                             OPENROUTER_API_KEY from Secret Manager on Cloud Run (K_SERVICE check,
                             same shape as anthropic_client.py's resolve_api_key()) but, unlike
                             that one, caches the fetched value per process after the first call -
                             this is also the function routers/query.py's own key resolution calls
                             through (services/tts.py's SECRET_ID is shared by both the TTS and
                             taxon-resolution call sites), so both benefit from the same cache
  waypoints.py              order_waypoints(species, center_lat, center_lon) — pure nearest-neighbour
                             route ordering from a center point (Slice 3); no I/O
  rate_limiter.py           slowapi Limiter instance + async custom 429 handler (reads the query text
                             from the still-unconsumed request body for REQ-017's log line, since
                             slowapi intercepts before FastAPI's own body parsing; branches by request
                             path so a rate-limited /api/narrate call logs narration_outcome rather
                             than a query event)
  query_budget.py           Global daily LLM-call counter, threading.Lock-guarded, date-based reset
  narration_budget.py       Same shape as query_budget.py, independent counter — see ADR-014
static/                    Built frontend assets, copied in at Docker build time — not in git
```

One structural rule: any future router must be registered in `create_app()` **before** the static-file mount — the mount matches every remaining path, so a route added after it would be unreachable. Noted inline in `main.py`.

Local dev needs a repo-root `.env` with `ANTHROPIC_API_KEY` (real narration/rollback LLM calls), `OPENROUTER_API_KEY` (real taxon-resolution LLM calls and real TTS calls), and `POSTHOG_PROJECT_TOKEN` (real server-side PostHog capture when testing with `consent=True`) — gitignored, loaded via `python-dotenv` in `main.py`. Tests that don't need real API access mock the service-layer functions at the router boundary (see `tests/conftest.py` for the rate-limiter/budget-counter/ai_observability-singleton reset fixtures needed because all three are process-global state — narration_budget.py's counter gets the same treatment). A separate `@pytest.mark.eval` tier (`tests/evals/`, `pytest.ini`) makes real Anthropic/GBIF/Wikipedia/OpenRouter calls — excluded from the default `pytest` run and CI, run explicitly via `pytest -m eval`. Covers happy-path taxon resolution (birds, plants, insects, fungi, turtles), adversarial cases (negation, off-topic, purely qualitative), mixed-taxa expansion (two- and three-way), the fish lay-term expansion (asserts the exact 7-group curated list), a real end-to-end pipeline case for both a single filter and a mixed-taxa pair — runs the real LLM call through the real local DuckDB query layer (ADR-017), then verifies the results independently against live GBIF `species/match` (an oracle on species identity, not on how the local query matched the taxon — still valid post-migration), an optional PostHog-capture check that auto-skips without `POSTHOG_PROJECT_TOKEN`, narration quality checks (length/timing/dash-pause/all-species-mentioned programmatic checks plus an LLM-judged faithfulness/habitat-claim pass, parametrized across three sample walks, plus a live content-safety refusal check — see ADR-014), and a narrative-to-audio check asserting the TTS response is valid, plausibly-sized audio. `tests/test_smoke_llm.py` still exercises the pre-ADR-017 taxon-resolution shape directly (not the router) and is deliberately left as-is pending a broader eval-strategy pass, rather than rewired piecemeal.

### Frontend (`app/frontend/`)

```
src/App.tsx                       Landing page, mounts MapView + ConsentBanner
src/components/MapView.tsx        Owns query/loading/result/area state. Renders a real navbar
                                     (app-shell → nav-bar: branding, AreaControl, QueryPanel) above
                                     the map, not a floating overlay on it (ADR-012 — this is also
                                     what keeps Leaflet-draw's own controls from being blocked).
                                     Area state is {mode: 'fixed'|'draw', polygon, center}; a
                                     persistent <Polygon> renders the current search area on the map
                                     regardless of draw-toolbar state. POSTs
                                     {query, distinctId, consent, polygon} to /api/query. Each
                                     species' hotspot is now a geohash cell, not a precise point
                                     (ADR-017) — draws one dashed footprint rectangle per distinct
                                     cell (several species can share one), spreads same-cell
                                     species to distinct points within it (circular offset scaled
                                     by cell_lat_err/cell_lon_err) rather than stacking identical
                                     markers, and reuses those same spread positions for the route
                                     line so it visibly passes through every numbered pin. Fires
                                     query_submitted/query_outcome.
src/components/AreaControl.tsx    Persistent, always-visible area-mode widget (ADR-012) — "Draw your
                                     own area" in fixed mode; "Redraw area"/"Explore Retiro Park" in
                                     draw mode. Reachable from any state, not gated behind a result.
src/components/DrawAreaControl.tsx Wires Leaflet-draw's native polygon/edit/delete toolbar into the
                                     map (useMap()). A shape auto-confirms on Leaflet's own
                                     CREATED/EDITED events once it passes validation — no custom
                                     confirm button (ADR-012). Renders GeolocationPrompt +
                                     AreaSizeWarning as children.
src/components/GeolocationPrompt.tsx "Use my location" (client-side only — never sent to the
                                     backend or logged) + a Skip control, shown once per session.
src/components/AreaSizeWarning.tsx Passive inline message when a drawn shape exceeds the area cap;
                                     no button — Leaflet's own edit/delete controls are how a user
                                     fixes it. The cap's rationale changed with ADR-017 (bounds
                                     candidate_cells' own grid-sampling cost now, not GBIF's rate
                                     limits) but the component/behaviour is unchanged.
src/lib/polygon.ts                Pure helpers: pointsToWkt/wktToPoints (Leaflet points <-> WKT),
                                     validatePolygonPoints (vertex count + bounding-area cap — must
                                     match models/query.py's backend validator's thresholds)
src/components/QueryPanel.tsx     Query form + loading/non-resolved-outcome message, rendered inline
                                     inside nav-bar (not its own floating/docked panel). No message is
                                     shown on a resolved outcome — the markers/route/results panel
                                     already communicate success. GBIF is no longer named anywhere
                                     in this UI (ADR-017) — the loading message is generic, and the
                                     gbif_unavailable outcome/status-link block was removed entirely
                                     since the backend can never return it any more.
src/components/ResultsPanel.tsx   Species list (common name primary, scientific name secondary) in
                                     route order. Each row shows two counts, stacked on the right,
                                     same size: the species' own hotspot-cell count and the
                                     area-wide total separately (they can differ substantially —
                                     the total sums occurrence_count across every candidate cell in
                                     the search area, not just the one cell shown on the map).
                                     Each row is an accordion — click/tap to expand a photo, with a
                                     no-image fallback when none was found (no GBIF species-page
                                     link any more — it needed GBIF's legacy numeric speciesKey,
                                     which the local dimension table's COL XR string id doesn't
                                     resolve on, ADR-017). Expanded state is owned by MapView
                                     (controlled component), not internal — so a marker click and a
                                     row click drive the same state and stay in sync. Real
                                     document-flow sidebar column on desktop; bottom-dock overlay
                                     with the same accordion behavior on mobile (index.css's 700px
                                     breakpoint). Renders nothing until a resolved outcome.
src/components/NarrationControl.tsx Inline control in ResultsPanel's header row, next to "Your walk"
                                     (wraps to its own line on mobile). Cycles idle ("Create
                                     narrative") -> loading -> a play/pause control once audio is
                                     ready; decodes the base64 response into a Blob and plays it via
                                     a hidden <audio> element. Transcript stays hidden behind a
                                     separate "Show transcript" toggle, never shown by default.
src/components/ConsentBanner.tsx  Accept/reject, persists choice in localStorage, gates PostHog
src/lib/posthog.ts                init (opt-out-by-default), optIn/optOut, getDistinctId(), hasConsent(),
                                     trackEvent(), exported CONSENT_KEY
```

No area-mode state (chosen mode, drawn polygon) persists across a page refresh — a refresh always returns to fixed/Retiro mode, consistent with the app's no-server-session-state design.

The dev server (`npm run dev`) proxies `/api` and `/health` to `localhost:8000` (`vite.config.ts`) so the two servers behave as one app locally. In production there's no proxy — the backend serves the built frontend from the same origin, so this is dev-only config.

### Infrastructure (`infra/`)

Terraform-managed: Cloud Run (`nature-quest-production`, `europe-west1`, public, `max_instance_count=2`, `POSTHOG_PROJECT_TOKEN`/`FACT_PARQUET_PATH`/`DIMENSION_PARQUET_PATH` env vars — the latter two point at the real `gs://` index-data bucket, no local default so dev can never accidentally hit it), Artifact Registry (Docker images), a dedicated Cloud Run runtime service account (`nature-quest-run`), Secret Manager secrets (`anthropic-api-key`, `openrouter-api-key` — values set out-of-band; `gcs-index-data-hmac-key-id`/`gcs-index-data-hmac-secret` — values generated by Terraform itself, ADR-018 — all four IAM-bound to `nature-quest-run`), a separate GCS bucket (`infra/gcs_index_data.tf`) serving the local DuckDB index data (ADR-016/ADR-017) with a least-privilege read/write split — `nature-quest-run` gets read-only access to that one bucket, a second, separate identity (`nature-quest-index-refresh`) gets write-only access for the (currently manual, eventually monthly-automated) data upload, and the two are never combined — Cloud Monitoring (`monitoring.tf` — an email notification channel, a log-based metric counting `query_outcome` log lines, and an alert policy firing on every one, per `ADR-010`), and Workload Identity Federation for GitHub Actions (see Deploy flow below). Remote state lives in a GCS bucket, bootstrapped manually once — see `infra/README.md` for that one-off step, `terraform apply` usage, and `infra/manual_deploy.sh` (a CI/CD-outage fallback that reproduces the build+deploy jobs locally).

## Request flow

```
Browser → Cloud Run (nature-quest-production) → FastAPI (main.py)
                                                    ├─ GET /health → routers/health.py
                                                    ├─ POST /api/query → routers/query.py
                                                    │    → services/ai_observability.py (consent-gated
                                                    │      client selection) → services/openrouter_taxon_client.py
                                                    │      (OpenRouter API, openai/gpt-oss-120b:nitro, real
                                                    │      key from Secret Manager — services/anthropic_client.py
                                                    │      stays available as a manual, tested rollback)
                                                    │    → services/species_index.py (candidate_cells,
                                                    │      then one DuckDB query per taxon filter against
                                                    │      Parquet on GCS via httpfs — HMAC key from
                                                    │      Secret Manager, ADR-018 — no live GBIF call
                                                    │      anywhere in this path, ADR-017)
                                                    │    → services/species_enrichment.py
                                                    │      (services/wikipedia_client.py image/extract
                                                    │      only, final species only — common_name already
                                                    │      joined by species_index.py)
                                                    │    → services/logging_client.py (structured log line)
                                                    ├─ POST /api/narrate → routers/narration.py
                                                    │    → services/ai_observability.py (consent-gated
                                                    │      client selection) → services/narration.py
                                                    │      (Anthropic API, real key from Secret Manager)
                                                    │    → services/tts.py (OpenRouter API, real key
                                                    │      from Secret Manager)
                                                    │    → services/logging_client.py (structured log line)
                                                    └─ everything else → static/ (built frontend)
```

The DuckDB/GCS connection and both OpenRouter clients shown above are no
longer built fresh on this path — they're built once per process (eagerly,
at startup, on Cloud Run) and reused across every request; see
`services/species_index.py`/`services/ai_observability.py`/`services/tts.py`
above and `docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md`.

## Deploy flow

```
PR opened  → lint/typecheck + unit tests (backend+frontend, parallel) + docker build validation
             (no deploy credentials touched at all — job gated out for pull_request events)
Merge to main → same checks again, then:
             docker build → push to Artifact Registry → gcloud run deploy → post-deploy smoke test
             (GET /health against the live URL — this is the only smoke-test check now that
             POST /api/interest is gone; no other endpoint is cheap/side-effect-free enough
             to smoke-test post-deploy, so this is a real, accepted coverage reduction)
```

Authentication uses Workload Identity Federation — no static GCP keys anywhere. The trust is scoped twice, not just once: the GitHub Actions workflow only runs the deploy job `if: push to main`, **and** GCP's own WIF provider independently rejects any token whose claims aren't `repository == AlexHandy1/nature-quest && ref == refs/heads/main` (`infra/wif.tf`) — so even a modified workflow file on a fork PR couldn't obtain deploy credentials.

`terraform apply` (Secret Manager, Cloud Run config) is a separate manual step, not part of CI/CD — see `infra/README.md`. If GitHub Actions itself is unavailable (a GitHub platform incident, not a repo config issue), `infra/manual_deploy.sh` reproduces the build+deploy jobs from a local machine, tagged by git SHA the same way CI tags images, so it composes cleanly with normal CI deploys rather than colliding with them.
