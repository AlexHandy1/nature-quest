# Architecture

A current, whole-system map. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file.

## What's live vs. what's not

**Phase 1 (production foundation)** and **Slice 2 (LLM guardrails + first GBIF query)** are both built and **deployed**. The landing page's primary interaction is now `QueryForm`: free text → LLM taxon resolution (real Anthropic call) → GBIF species list, behind per-IP rate limiting and a daily LLM-call budget guardrail, with the Anthropic API key fetched explicitly from Secret Manager at container startup (REQ-005) — never a plain env var in production. Structured operational logging (REQ-017) and consent-gated PostHog observability (REQ-018/019, client- and server-side) are both live. Full detail: `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`.

**Slice 3 (multi-taxon query, in progress)**: the query pipeline now handles mixed-taxa requests (e.g. "birds and plants") and lay terms with no single GBIF rank (e.g. "fish", "reptiles") — previously these returned nothing. The LLM resolves to a *list* of taxon filters, each resolved and fetched independently, then merged via quota/round-robin — see `docs/decisions/ADR-011-multi-taxon-query-resolution-strategy.md`. Clustering and route ordering (the rest of Slice 3's original scope) are not yet built.

**Still outstanding from Slice 2's original scope**: basic GCP uptime/error-rate alerting (REQ-025-027) — not yet built. A *different* alert was built first instead: a real-time email on every `/api/query` submission (`infra/monitoring.tf` — log-based metric + alert policy), a deliberate, explicitly-scoped short-term deviation that will not scale past near-zero traffic — see `docs/decisions/ADR-010-realtime-per-query-alerting.md`. `POST /api/interest`, `InterestSubmission`, and `InterestForm.tsx` are kept in place, dormant/unreferenced, pending a future cleanup slice (`CON-001`) — do not assume they're still the primary interaction.

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
routers/interest.py        GET /health, POST /api/interest (Phase 1, still reachable, unused by frontend)
routers/query.py           POST /api/query (Slice 2, extended Slice 3) — rate-limited; validate →
                             daily budget → LLM resolve to a list of taxon filters (+ token usage
                             capture) → per-filter key resolve (_resolve_taxon_keys — drops/surfaces
                             any filter that fails to resolve as unresolvedGroups) → GBIF fetch
                             across all resolved filters → 4-outcome response, structured log line
                             on every branch (REQ-017)
models/interest.py         InterestSubmission (Pydantic) — query only, no PII
models/query.py            QueryRequest (Pydantic) — query, distinctId, consent (default False)
services/
  logging_client.py        log_interest_submission(), log_query_outcome() — structured Cloud Logging
                             writes (JsonLogFormatter, main.py)
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
  gbif_client.py            fetch_top_species(taxon_filters, polygon=GBIF_POLYGON) — one
                             occurrence/search call per filter, ranked per group, merged via
                             quota/round-robin (_select_species_across_groups, see ADR-011);
                             fixed Retiro polygon by default, scale-guard, retry, ranking
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
src/App.tsx                       Landing page, mounts QueryForm + ConsentBanner
src/components/QueryForm.tsx      One-shot: idle (input+button) → loading (both disabled) → terminal
                                     result. POSTs {query, distinctId, consent} to /api/query. Renders
                                     all 4 outcomes + both 429 variants distinctly; species list is
                                     name+count only (no map yet). Fires query_submitted/query_outcome.
src/components/InterestForm.tsx   Dormant, unreferenced by App.tsx — kept per CON-001, not deleted
src/components/ConsentBanner.tsx  Accept/reject, persists choice in localStorage, gates PostHog
src/lib/posthog.ts                init (opt-out-by-default), optIn/optOut, getDistinctId(), hasConsent(),
                                     trackEvent(), exported CONSENT_KEY
```

The dev server (`npm run dev`) proxies `/api` and `/health` to `localhost:8000` (`vite.config.ts`) so the two servers behave as one app locally. In production there's no proxy — the backend serves the built frontend from the same origin, so this is dev-only config.

### Infrastructure (`infra/`)

Terraform-managed: Cloud Run (`nature-quest-production`, `europe-west1`, public, `max_instance_count=2`, `POSTHOG_PROJECT_TOKEN` env var), Artifact Registry (Docker images), a dedicated Cloud Run runtime service account, a Secret Manager secret (`anthropic-api-key`, IAM-bound to that service account — value set out-of-band, never in Terraform), Cloud Monitoring (`monitoring.tf` — an email notification channel, a log-based metric counting `query_outcome` log lines, and an alert policy firing on every one, per `ADR-010`), and Workload Identity Federation for GitHub Actions (see Deploy flow below). Remote state lives in a GCS bucket, bootstrapped manually once — see `infra/README.md` for that one-off step, `terraform apply` usage, and `infra/manual_deploy.sh` (a CI/CD-outage fallback that reproduces the build+deploy jobs locally).

### CI/CD (`.github/workflows/ci-cd.yml`)

## Request flow

```
Browser → Cloud Run (nature-quest-production) → FastAPI (main.py)
                                                    ├─ GET /health, POST /api/interest → routers/interest.py
                                                    ├─ POST /api/query → routers/query.py
                                                    │    → services/ai_observability.py (consent-gated
                                                    │      client selection) → services/anthropic_client.py
                                                    │      (Anthropic API, real key from Secret Manager)
                                                    │    → services/taxon_resolution.py (GBIF species/match)
                                                    │    → services/gbif_client.py (GBIF occurrence/search)
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
             (GET /health, POST /api/interest against the live URL)
```

Authentication uses Workload Identity Federation — no static GCP keys anywhere. The trust is scoped twice, not just once: the GitHub Actions workflow only runs the deploy job `if: push to main`, **and** GCP's own WIF provider independently rejects any token whose claims aren't `repository == AlexHandy1/nature-quest && ref == refs/heads/main` (`infra/wif.tf`) — so even a modified workflow file on a fork PR couldn't obtain deploy credentials.

`terraform apply` (Secret Manager, Cloud Run config) is a separate manual step, not part of CI/CD — see `infra/README.md`. If GitHub Actions itself is unavailable (a GitHub platform incident, not a repo config issue), `infra/manual_deploy.sh` reproduces the build+deploy jobs from a local machine, tagged by git SHA the same way CI tags images, so it composes cleanly with normal CI deploys rather than colliding with them.

## Where to go deeper

| Question | Look here |
|---|---|
| Why was X chosen over Y? | `docs/decisions/ADR-*.md` |
| Exact requirements/acceptance criteria for Phase 1 | `docs/specs/spec-infrastructure-production-foundation-300726.md` |
| Exact requirements/acceptance criteria for Slice 2 (LLM guardrails, GBIF query) | `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md` |
| What's the product vision, phases, personas? | `docs/prds/nature-quest-prd-300726.md` |
| What happened in a past session? | `docs/status_docs/WORK_SUMMARY_*.md` (date order) |
| How was the pipeline concept validated? | `prototypes/README.md` |
| How do I run this locally / deploy it? | root `README.md`, `infra/README.md` |
