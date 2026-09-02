# Architecture

A current, whole-system map of what's built and how it works today — not a roadmap. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file. Product scope, phasing, and what's planned or deferred live in `docs/prds/`, `docs/specs/`, and `docs/status_docs/`, not here.

## Overview

Nature Quest is a FastAPI backend + Vite/React frontend that turns a natural-language request and a drawn search area into a GBIF-backed nature walk. `MapView` is the whole product surface: a user draws or accepts a default search area, submits free text, and gets back a route-ordered species list, each enriched with a common name, photo, and GBIF link.

Request path: free text → LLM taxon resolution (OpenRouter, model-agnostic client — currently `google/gemini-3.7-flash`) → per-taxon GBIF occurrence lookup → nearest-neighbour route ordering → species enrichment (common name + Wikipedia image) → response rendered as map markers plus a results panel. This sits behind per-IP rate limiting and a daily LLM-call budget guardrail, with structured operational logging and consent-gated PostHog observability (client- and server-side).

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
                             slowapi rate-limiter wiring, .env loading (load_dotenv), static-file mount
routers/health.py          GET /health
routers/query.py           POST /api/query (Slice 2, extended Slice 3) — rate-limited; validate →
                             daily budget → LLM resolve to a list of taxon filters (+ token usage
                             capture) → per-filter key resolve (_resolve_taxon_keys — drops/surfaces
                             any filter that fails to resolve as unresolvedGroups) → GBIF fetch
                             across all resolved filters → 4-outcome response, structured log line
                             on every branch (REQ-017)
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
                             area (Slice 9), reusing gbif_client.parse_polygon_vertices rather than
                             duplicating WKT-parsing logic
models/narration.py         NarrateRequest (Pydantic) — species (exactly TOP_SPECIES_COUNT
                             SpeciesInput entries, each field length-bounded), distinctId, consent
services/
  logging_client.py        log_query_outcome() plus per-pipeline-stage log lines (log_query_submitted
                             — now also takes the submitted polygon, REQ-015 — log_llm_taxon_filters_resolved,
                             log_species_selected, log_waypoints_ordered, log_species_enriched) —
                             structured Cloud Logging writes (JsonLogFormatter, main.py), all stamped
                             with distinct_id so they can be cross-referenced with PostHog's Activity view
  anthropic_client.py      TAXON_GUIDANCE + QUERY_SCHEMA_TOOL, resolve_taxon_filters() (accepts an
                             optional on_response callback + **extra_kwargs passthrough) — returns a
                             list of {taxonRank, taxonValue}, empty if no signal. System prompt
                             directly teaches two multi-entry lay-term expansions (fish, reptiles) —
                             see ADR-011 — plus a non-standard rule forcing Testudines to class rank
                             (not the biologically-conventional order), needed to keep a
                             model-agnostic taxon-resolution prompt correct across providers. Still
                             the single source of truth for TAXON_GUIDANCE/QUERY_SCHEMA_TOOL (imported,
                             not copied, by openrouter_taxon_client.py) and retained, unused by
                             routers/query.py, as narration.py's own Anthropic call site plus a manual,
                             tested rollback path for taxon resolution (swap the constant/import back —
                             see the OpenRouter spec). build_client(), resolve_api_key() (Secret Manager
                             on Cloud Run via K_SERVICE check, local ANTHROPIC_API_KEY env var
                             otherwise), _fetch_api_key_from_secret_manager()
  openrouter_taxon_client.py Model-agnostic taxon-resolution client used by routers/query.py today —
                             MODEL constant (google/gemini-3.7-flash; the only place a model name
                             appears — swapping providers/models is a one-constant change plus
                             re-running the taxon-resolution/full-pipeline eval suites, no other code
                             changes), QUERY_SCHEMA_TOOL_OPENAI (built once at import from
                             anthropic_client.py's QUERY_SCHEMA_TOOL, OpenAI tool-calling shape),
                             resolve_taxon_filters() — same signature as anthropic_client.py's function,
                             calls client.chat.completions.create() with forced tool choice, parses
                             taxonFilters out of the tool-call JSON, raises rather than silently
                             returning [] on a missing/malformed tool call. No explicit `provider`
                             routing preference is set, so OpenRouter's default "Balanced" mode picks
                             the backend/tier per call — real measured cost came in ~2x the original
                             prototyping estimate as a result; see ADR-015.
  ai_observability.py      build_client(consent, distinct_id, api_key) — returns a plain
                             anthropic.Anthropic when consent=False, or a posthog.ai.anthropic.Anthropic
                             wrapper (per-call posthog_distinct_id, full $ai_input/$ai_output_choices
                             capture) when consent=True; still used by narration.py's Anthropic call
                             site. build_openrouter_client(consent, distinct_id, api_key) — same
                             consent-gating shape, pointed at OpenRouter (base_url=
                             https://openrouter.ai/api/v1): a plain openai.OpenAI when consent=False, or
                             posthog.ai.openai.OpenAI when consent=True — used by routers/query.py's
                             taxon resolution. Both branches share one lazily-built singleton PostHog
                             client from POSTHOG_PROJECT_TOKEN/POSTHOG_HOST env vars. Note:
                             posthog.ai.openai.OpenAI always stamps $ai_provider: "openai" on captured
                             events regardless of base_url — filter/group PostHog insights for this call
                             site by $ai_model or $ai_base_url instead. See ADR-009.
  taxon_resolution.py      resolve_taxon_key() — live GBIF species/match only, no local cache (see
                             ADR-011); called once per filter, sequentially, by routers/query.py
  gbif_client.py            fetch_top_species(taxon_filters, polygon) — one occurrence/search call per
                             filter, ranked per group, merged via quota/round-robin
                             (_select_species_across_groups, see ADR-011); the caller (routers/query.py)
                             always passes an explicit polygon now (Slice 9) — GBIF_POLYGON remains as
                             the Retiro constant/default-parameter value, not a fallback the router
                             relies on. Also: parse_polygon_vertices/polygon_centroid (shared WKT
                             parsing, used by both this module and models/query.py's validator),
                             per-species NxN density-cluster hotspot (_cluster_species_hotspot,
                             Slice 3), scale-guard, retry, ranking, fetch_common_name(species_key) —
                             GBIF vernacularNames, majority-vote across English-tagged names (not
                             first-match — GBIF has no preferred-name flag and mixes in rare
                             abbreviations, see ADR-013)
  wikipedia_client.py       fetch_species_summary(common_name, scientific_name) — Wikipedia summary
                             API, common name first then scientific name fallback on a missing/
                             disambiguation article; returns {image_url, extract} in one fetch (the
                             extract feeds narration grounding, ADR-014). See ADR-013 for why
                             Wikipedia over GBIF's own occurrence photos.
  species_enrichment.py     enrich_species(species_list) — adds common_name + image_url/extract to
                             each of the final selected species (not every candidate scanned during
                             ranking), concurrently across species (capped at 3, same pattern as
                             elsewhere)
  narration.py              generate_narrative(species_list, client, on_response=None,
                             **extra_kwargs) — grounds a short narrative in each species' Wikipedia
                             extract (GROUNDED_FACTS_GUIDANCE), includes a content-safety refusal path
                             (returns None on refusal — see ADR-014), sanitizes TTS-unfriendly dash
                             pauses deterministically after generation
  tts.py                    synthesize_speech(text, api_key, voice) — OpenRouter/Kokoro-82M
                             text-to-speech, mp3 response_format. resolve_api_key() mirrors
                             anthropic_client.py's Secret Manager pattern for OPENROUTER_API_KEY
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

Local dev needs a repo-root `.env` with `ANTHROPIC_API_KEY` (real narration/rollback LLM calls), `OPENROUTER_API_KEY` (real taxon-resolution LLM calls and real TTS calls), and `POSTHOG_PROJECT_TOKEN` (real server-side PostHog capture when testing with `consent=True`) — gitignored, loaded via `python-dotenv` in `main.py`. Tests that don't need real API access mock the service-layer functions at the router boundary (see `tests/conftest.py` for the rate-limiter/budget-counter/ai_observability-singleton reset fixtures needed because all three are process-global state — narration_budget.py's counter gets the same treatment). A separate `@pytest.mark.eval` tier (`tests/evals/`, `pytest.ini`) makes real Anthropic/GBIF/Wikipedia/OpenRouter calls — excluded from the default `pytest` run and CI, run explicitly via `pytest -m eval`. Covers happy-path taxon resolution (birds, plants, insects, fungi, turtles), adversarial cases (negation, off-topic, purely qualitative), mixed-taxa expansion (two- and three-way), the fish lay-term expansion (asserts the exact 7-group curated list), a real end-to-end GBIF pipeline case for both a single filter and a mixed-taxa pair (verified against real GBIF data via independent `species/match` calls, not production's own resolver), an optional PostHog-capture check that auto-skips without `POSTHOG_PROJECT_TOKEN`, narration quality checks (length/timing/dash-pause/all-species-mentioned programmatic checks plus an LLM-judged faithfulness/habitat-claim pass, parametrized across three sample walks, plus a live content-safety refusal check — see ADR-014), and a narrative-to-audio check asserting the TTS response is valid, plausibly-sized audio.

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
                                     {query, distinctId, consent, polygon} to /api/query, plots
                                     route-ordered numbered markers + a dashed connector line on a
                                     resolved outcome. Fires query_submitted/query_outcome.
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
                                     fixes it.
src/lib/polygon.ts                Pure helpers: pointsToWkt/wktToPoints (Leaflet points <-> WKT),
                                     validatePolygonPoints (vertex count + bounding-area cap — must
                                     match models/query.py's backend validator's thresholds)
src/components/QueryPanel.tsx     Query form + loading/non-resolved-outcome message, rendered inline
                                     inside nav-bar (not its own floating/docked panel). No message is
                                     shown on a resolved outcome — the markers/route/results panel
                                     already communicate success.
src/components/ResultsPanel.tsx   Species list (common name primary, scientific name secondary,
                                     observation count) in route order. Each row is an accordion —
                                     click/tap to expand a photo + GBIF species-page link
                                     (gbif.org/species/{species_key}), with a no-image fallback when
                                     none was found. Expanded state is owned by MapView (controlled
                                     component), not internal — so a marker click and a row click drive
                                     the same state and stay in sync. Real document-flow sidebar column
                                     on desktop; bottom-dock overlay with the same accordion behavior on
                                     mobile (index.css's 700px breakpoint). Renders nothing until a
                                     resolved outcome.
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

Terraform-managed: Cloud Run (`nature-quest-production`, `europe-west1`, public, `max_instance_count=2`, `POSTHOG_PROJECT_TOKEN` env var), Artifact Registry (Docker images), a dedicated Cloud Run runtime service account, Secret Manager secrets (`anthropic-api-key`, `openrouter-api-key` — both IAM-bound to that service account, values set out-of-band, never in Terraform), Cloud Monitoring (`monitoring.tf` — an email notification channel, a log-based metric counting `query_outcome` log lines, and an alert policy firing on every one, per `ADR-010`), and Workload Identity Federation for GitHub Actions (see Deploy flow below). Remote state lives in a GCS bucket, bootstrapped manually once — see `infra/README.md` for that one-off step, `terraform apply` usage, and `infra/manual_deploy.sh` (a CI/CD-outage fallback that reproduces the build+deploy jobs locally).

## Request flow

```
Browser → Cloud Run (nature-quest-production) → FastAPI (main.py)
                                                    ├─ GET /health → routers/health.py
                                                    ├─ POST /api/query → routers/query.py
                                                    │    → services/ai_observability.py (consent-gated
                                                    │      client selection) → services/openrouter_taxon_client.py
                                                    │      (OpenRouter API, google/gemini-3.7-flash, real
                                                    │      key from Secret Manager — services/anthropic_client.py
                                                    │      stays available as a manual, tested rollback)
                                                    │    → services/taxon_resolution.py (GBIF species/match)
                                                    │    → services/gbif_client.py (GBIF occurrence/search)
                                                    │    → services/species_enrichment.py
                                                    │      (services/gbif_client.py vernacularNames +
                                                    │      services/wikipedia_client.py, final species only)
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
