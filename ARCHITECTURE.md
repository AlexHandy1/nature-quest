# Architecture

A current, whole-system map. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file.

## What's live vs. what's not

**Phase 1 (production foundation)** and **Slice 2 (LLM guardrails + first GBIF query)** are both built and **deployed**. The landing page's primary interaction is now `MapView`: free text → LLM taxon resolution (real Anthropic call) → GBIF species list, behind per-IP rate limiting and a daily LLM-call budget guardrail, with the Anthropic API key fetched explicitly from Secret Manager at container startup (REQ-005) — never a plain env var in production. Structured operational logging (REQ-017) and consent-gated PostHog observability (REQ-018/019, client- and server-side) are both live. Full detail: `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`.

**Slice 3 (multi-taxon query + clustering + route ordering)** is **built and deployed**. The query pipeline handles mixed-taxa requests (e.g. "birds and plants") and lay terms with no single GBIF rank (e.g. "fish", "reptiles") — the LLM resolves to a *list* of taxon filters, each resolved and fetched independently, then merged via quota/round-robin — see `docs/decisions/ADR-011-multi-taxon-query-resolution-strategy.md`. Each species' hotspot is an NxN density-cluster (not a plain average), and the response's species list is nearest-neighbour route order from the search area's centroid, via `services/waypoints.py`.

**Slice 9 (draw-your-own-area)** is **built and deployed**. `/api/query`'s `polygon` field is required (no backend default/fixed-area fallback); the frontend always sends an explicit WKT polygon, either the fixed Retiro constant or a user-drawn one. The frontend's actual area-selection UX diverges materially from the slice's original spec — a persistent, always-changeable widget (`AreaControl`) replaced a one-time popup/funnel, and Leaflet-draw's own native controls replaced a custom "Confirm Area" button — see `docs/decisions/ADR-012-area-selection-persistent-widget-not-popup-funnel.md`. Automated end-to-end coverage for the drawing flow itself was not achieved (see `tests/e2e_web_smoke_test.py`'s "known gaps" comment) — manual verification only.

**Species detail enrichment (common name, image, GBIF link)** is **built and deployed**. Each of the final ~5 selected species is enriched with a GBIF vernacular name and a Wikipedia article image (`services/species_enrichment.py`) before the response is sent; the frontend's `ResultsPanel` renders this as an accordion (tap/click a row to expand photo + GBIF link), with the same interaction model on both a desktop sidebar and a mobile bottom-dock overlay — see `docs/decisions/ADR-013-species-image-source-wikipedia-not-gbif-media.md` for why the image comes from Wikipedia rather than GBIF's own occurrence photos.

**Still outstanding from Slice 2's original scope**: basic GCP uptime/error-rate alerting (REQ-025-027) — not yet built. A *different* alert was built first instead: a real-time email on every `/api/query` submission (`infra/monitoring.tf` — log-based metric + alert policy), a deliberate, explicitly-scoped short-term deviation that will not scale past near-zero traffic — see `docs/decisions/ADR-010-realtime-per-query-alerting.md`. `POST /api/interest`, `InterestSubmission`, and `InterestForm.tsx` (dormant since `CON-001`) have since been deleted entirely, along with their tests and the CI/CD smoke-test check that POSTed to `/api/interest`.

**REQ-019's implementation deviates from the spec's original design**: the spec specifies OpenTelemetry (`AnthropicInstrumentor` + `PostHogSpanProcessor`); the actual build uses `posthog.ai.anthropic.Anthropic`, a wrapper client — see `docs/decisions/ADR-009-posthog-ai-observability-wrapper-client.md` for why.

The fuller NL-query pipeline beyond Slice 2's scope (waypoint ordering, route generation, narrative, map rendering) is **not built** — it was validated as a throwaway prototype (`prototypes/`, untouched, never deployed) and is later PRD slices, not yet started. Don't assume anything under `app/` implements the full prototype pipeline — Slice 2 only goes as far as a species list.

Within Phase 1 itself, two requirements from the original spec are **deliberately deferred**, not missing by oversight:
- **Cloud Armor + load balancer** (Cloud Run `max_instance_count=2` used as an interim cost-creep guardrail instead) — see `docs/decisions/ADR-007-analytics-consent-abuse-guardrails.md`'s implementation notes.

Full requirement-level detail and status: `docs/specs/spec-infrastructure-production-foundation-300726.md` (search for `[DEFERRED]`).

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
models/query.py            QueryRequest (Pydantic) — query, distinctId, consent (default False),
                             polygon (required WKT string, no backend default — REQ-012). A
                             field_validator enforces a minimum vertex count and a maximum bounding
                             area (Slice 9), reusing gbif_client.parse_polygon_vertices rather than
                             duplicating WKT-parsing logic
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
                             see ADR-011. build_client(), resolve_api_key() (Secret Manager on Cloud
                             Run via K_SERVICE check, local ANTHROPIC_API_KEY env var otherwise),
                             _fetch_api_key_from_secret_manager()
  ai_observability.py      build_client(consent, distinct_id, api_key) — returns a plain
                             anthropic.Anthropic when consent=False, or a posthog.ai.anthropic.Anthropic
                             wrapper (per-call posthog_distinct_id, full $ai_input/$ai_output_choices
                             capture) when consent=True. Lazily builds/reuses a singleton PostHog client
                             from POSTHOG_PROJECT_TOKEN/POSTHOG_HOST env vars. See ADR-009.
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
  wikipedia_client.py       fetch_species_image(common_name, scientific_name) — Wikipedia summary API,
                             common name first then scientific name fallback on a missing/disambiguation
                             article. See ADR-013 for why Wikipedia over GBIF's own occurrence photos.
  species_enrichment.py     enrich_species(species_list) — adds common_name + image_url to each of the
                             final selected species (not every candidate scanned during ranking),
                             concurrently across species (capped at 3, same pattern as elsewhere)
  waypoints.py              order_waypoints(species, center_lat, center_lon) — pure nearest-neighbour
                             route ordering from a center point (Slice 3); no I/O
  rate_limiter.py           slowapi Limiter instance + async custom 429 handler (reads the query text
                             from the still-unconsumed request body for REQ-017's log line, since
                             slowapi intercepts before FastAPI's own body parsing)
  query_budget.py           Global daily LLM-call counter, threading.Lock-guarded, date-based reset
static/                    Built frontend assets, copied in at Docker build time — not in git
```

One structural rule: any future router must be registered in `create_app()` **before** the static-file mount — the mount matches every remaining path, so a route added after it would be unreachable. Noted inline in `main.py`.

Local dev needs a repo-root `.env` with `ANTHROPIC_API_KEY` (real LLM calls) and `POSTHOG_PROJECT_TOKEN` (real server-side PostHog capture when testing with `consent=True`) — gitignored, loaded via `python-dotenv` in `main.py`. Tests that don't need real API access mock the service-layer functions at the router boundary (see `tests/conftest.py` for the rate-limiter/budget-counter/ai_observability-singleton reset fixtures needed because all three are process-global state). A separate `@pytest.mark.eval` tier (`tests/evals/`, `pytest.ini`) makes real Anthropic/GBIF calls — excluded from the default `pytest` run and CI, run explicitly via `pytest -m eval`. Covers happy-path taxon resolution (birds, plants, insects, fungi, turtles), adversarial cases (negation, off-topic, purely qualitative), mixed-taxa expansion (two- and three-way), the fish lay-term expansion (asserts the exact 7-group curated list), a real end-to-end GBIF pipeline case for both a single filter and a mixed-taxa pair (verified against real GBIF data via independent `species/match` calls, not production's own resolver), and an optional PostHog-capture check that auto-skips without `POSTHOG_PROJECT_TOKEN`.

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
src/components/ConsentBanner.tsx  Accept/reject, persists choice in localStorage, gates PostHog
src/lib/posthog.ts                init (opt-out-by-default), optIn/optOut, getDistinctId(), hasConsent(),
                                     trackEvent(), exported CONSENT_KEY
```

No area-mode state (chosen mode, drawn polygon) persists across a page refresh — a refresh always returns to fixed/Retiro mode, consistent with the app's no-server-session-state design.

The dev server (`npm run dev`) proxies `/api` and `/health` to `localhost:8000` (`vite.config.ts`) so the two servers behave as one app locally. In production there's no proxy — the backend serves the built frontend from the same origin, so this is dev-only config.

### Infrastructure (`infra/`)

Terraform-managed: Cloud Run (`nature-quest-production`, `europe-west1`, public, `max_instance_count=2`, `POSTHOG_PROJECT_TOKEN` env var), Artifact Registry (Docker images), a dedicated Cloud Run runtime service account, a Secret Manager secret (`anthropic-api-key`, IAM-bound to that service account — value set out-of-band, never in Terraform), Cloud Monitoring (`monitoring.tf` — an email notification channel, a log-based metric counting `query_outcome` log lines, and an alert policy firing on every one, per `ADR-010`), and Workload Identity Federation for GitHub Actions (see Deploy flow below). Remote state lives in a GCS bucket, bootstrapped manually once — see `infra/README.md` for that one-off step, `terraform apply` usage, and `infra/manual_deploy.sh` (a CI/CD-outage fallback that reproduces the build+deploy jobs locally).

### CI/CD (`.github/workflows/ci-cd.yml`)

## Request flow

```
Browser → Cloud Run (nature-quest-production) → FastAPI (main.py)
                                                    ├─ GET /health → routers/health.py
                                                    ├─ POST /api/query → routers/query.py
                                                    │    → services/ai_observability.py (consent-gated
                                                    │      client selection) → services/anthropic_client.py
                                                    │      (Anthropic API, real key from Secret Manager)
                                                    │    → services/taxon_resolution.py (GBIF species/match)
                                                    │    → services/gbif_client.py (GBIF occurrence/search)
                                                    │    → services/species_enrichment.py
                                                    │      (services/gbif_client.py vernacularNames +
                                                    │      services/wikipedia_client.py, final species only)
                                                    │    → services/logging_client.py (structured log line)
                                                    └─ everything else → static/ (built frontend)
```

Note: `/healthz` is a **reserved path on Cloud Run** — Google's front-end intercepts it before it reaches any container. The health-check endpoint is `/health`. See `docs/decisions/ADR-005-iac-terraform-cicd-github-actions.md`'s implementation note if this comes up again.

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

## Where to go deeper

| Question | Look here |
|---|---|
| Why was X chosen over Y? | `docs/decisions/ADR-*.md` |
| Exact requirements/acceptance criteria for Phase 1 | `docs/specs/spec-infrastructure-production-foundation-300726.md` |
| Exact requirements/acceptance criteria for Slice 2 (LLM guardrails, GBIF query) | `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md` |
| Original design + built-vs-diverged detail for Slice 9 (draw-your-own-area) | `docs/specs/spec-tool-draw-your-own-area-100826.md`, `docs/decisions/ADR-012-area-selection-persistent-widget-not-popup-funnel.md` |
| Why species images come from Wikipedia, not GBIF occurrence photos | `docs/decisions/ADR-013-species-image-source-wikipedia-not-gbif-media.md` |
| What's the product vision, phases, personas? | `docs/prds/nature-quest-prd-300726.md` |
| What happened in a past session? | `docs/status_docs/WORK_SUMMARY_*.md` (date order) |
| How was the pipeline concept validated? | `prototypes/README.md` |
| How do I run this locally / deploy it? | root `README.md`, `infra/README.md` |
