# Architecture

A current, whole-system map. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file.

## What's live vs. what's not

**Phase 1 (production foundation)** is built and deployed: a "coming soon" landing page with a consent-gated interest-capture form.

**Slice 2 (LLM guardrails + first GBIF query)** is built and locally tested, but **not deployed**: `POST /api/query` (free-text → LLM taxon resolution → GBIF species list) works end-to-end against the real Anthropic API and real GBIF, with per-IP rate limiting and a daily LLM-call budget guardrail. Still outstanding before this can deploy: the Anthropic API key currently reads from a local `.env` (`services/anthropic_client.py`'s `build_client()`) — the Secret Manager explicit-fetch branch (REQ-005) is a `NotImplementedError` placeholder, gated behind `K_SERVICE` so it can't silently run half-built in production. Also outstanding: `infra/secret_manager.tf` provisioning, structured operational logging (REQ-017), PostHog server/client observability (REQ-018/019), basic GCP alerting (REQ-025-027), and the frontend swap from `InterestForm` to a query box (`CON-001`). Full detail: `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`.

The fuller NL-query pipeline beyond Slice 2's scope (waypoint ordering, route generation, narrative, map rendering) is **not built** — it was validated as a throwaway prototype (`prototypes/`, untouched, never deployed) and is later PRD slices, not yet started. Don't assume anything under `app/` implements the full prototype pipeline — Slice 2 only goes as far as a species list.

Within Phase 1 itself, two requirements from the original spec are **deliberately deferred**, not missing by oversight:
- **Server-side PostHog capture** (client-side only for now) — see `docs/decisions/ADR-007-analytics-consent-abuse-guardrails.md`.
- **Cloud Armor + load balancer** (Cloud Run `max_instance_count=2` used as an interim cost-creep guardrail instead) — see the same ADR-007 implementation notes.

Full requirement-level detail and status: `docs/specs/spec-infrastructure-production-foundation-300726.md` (search for `[DEFERRED]`).

## Components

```
app/backend/    FastAPI — API + serves the built frontend as static files
app/frontend/   Vite + React + TypeScript — landing page, interest form, consent banner
infra/          Terraform — GCP infrastructure (Cloud Run, Artifact Registry, IAM, WIF)
.github/        GitHub Actions CI/CD
prototypes/     Throwaway validation code — untouched, never deployed, not part of this architecture
```

### Backend (`app/backend/`)

```
main.py                    create_app(): FastAPI instance, JSON stdout logging config,
                             slowapi rate-limiter wiring, .env loading (load_dotenv), static-file mount
routers/interest.py        GET /health, POST /api/interest (Phase 1, still reachable, unused by frontend)
routers/query.py           POST /api/query (Slice 2) — rate-limited; validate → daily budget →
                             LLM resolve → taxon key resolve → GBIF fetch → 4-outcome response
models/interest.py         InterestSubmission (Pydantic) — query only, no PII
models/query.py            QueryRequest (Pydantic) — query + distinctId, max-length/whitespace validation
services/
  logging_client.py        Structured Cloud Logging write (every valid interest submission)
  anthropic_client.py      TAXON_GUIDANCE + QUERY_SCHEMA_TOOL, resolve_taxon_filter(), build_client()
                             (env-based key source split — see "What's live" above)
  taxon_resolution.py      resolve_taxon_key() — live GBIF species/match only, no local cache (see spec §9)
  gbif_client.py            fetch_top_species() — fixed Retiro polygon/year, scale-guard, retry, ranking
  rate_limiter.py           slowapi Limiter instance + custom 429 handler (shared across routers)
  query_budget.py           Global daily LLM-call counter, threading.Lock-guarded, date-based reset
static/                    Built frontend assets, copied in at Docker build time — not in git
```

One structural rule: any future router must be registered in `create_app()` **before** the static-file mount — the mount matches every remaining path, so a route added after it would be unreachable. Noted inline in `main.py`.

Local dev needs a repo-root `.env` with `ANTHROPIC_API_KEY` for `POST /api/query` to make real LLM calls — gitignored, loaded via `python-dotenv` in `main.py`. Tests that don't need real API access mock the service-layer functions at the router boundary (see `tests/conftest.py` for the rate-limiter/budget-counter reset fixtures needed because both are process-global state). A separate `@pytest.mark.eval` tier (`tests/test_smoke_llm.py`, `pytest.ini`) makes real Anthropic calls — excluded from the default `pytest` run and CI, run explicitly via `pytest -m eval`.

### Frontend (`app/frontend/`)

```
src/App.tsx                       Landing page, mounts InterestForm + ConsentBanner
src/components/InterestForm.tsx   Submits {query} to POST /api/interest, success/error states
src/components/ConsentBanner.tsx  Accept/reject, persists choice in localStorage, gates PostHog
src/lib/posthog.ts                Thin wrapper: init (opt-out-by-default), optIn/optOut
```

The dev server (`npm run dev`) proxies `/api` and `/health` to `localhost:8000` (`vite.config.ts`) so the two servers behave as one app locally. In production there's no proxy — the backend serves the built frontend from the same origin, so this is dev-only config.

### Infrastructure (`infra/`)

Terraform-managed: Cloud Run (`nature-quest-production`, `europe-west1`, public, `max_instance_count=2`), Artifact Registry (Docker images), a dedicated Cloud Run runtime service account, an empty Secret Manager scaffold (no secret exists yet), and Workload Identity Federation for GitHub Actions (see Deploy flow below). Remote state lives in a GCS bucket, bootstrapped manually once — see `infra/README.md` for that one-off step and for manual-deploy commands used before CI/CD existed.

### CI/CD (`.github/workflows/ci-cd.yml`)

## Request flow

```
Browser → Cloud Run (nature-quest-production) → FastAPI (main.py)
                                                    ├─ GET /health, POST /api/interest → routers/interest.py
                                                    ├─ POST /api/query (local dev only — not yet deployed,
                                                    │    see "What's live" above) → routers/query.py
                                                    │    → services/anthropic_client.py (Anthropic API)
                                                    │    → services/taxon_resolution.py (GBIF species/match)
                                                    │    → services/gbif_client.py (GBIF occurrence/search)
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
