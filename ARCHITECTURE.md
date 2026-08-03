# Architecture

A current, whole-system map. For the detailed "why" behind any decision here, follow the linked ADR/spec rather than expecting the reasoning restated in this file.

## What's live vs. what's not

Only **Phase 1 (production foundation)** is built and deployed: a "coming soon" landing page with a consent-gated interest-capture form. The real NL-query pipeline (species → route → narrative → map) is **not built** — it was validated as a throwaway prototype (`prototypes/`, untouched, never deployed) and is Phase 2, not yet started. Don't assume anything under `app/` implements the prototype's pipeline — it doesn't yet.

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
main.py              create_app(): FastAPI instance, JSON stdout logging config, static-file mount
routers/interest.py  GET /health, POST /api/interest
models/interest.py   InterestSubmission (Pydantic) — query only, no PII
services/
  logging_client.py  Structured Cloud Logging write (every valid submission)
static/              Built frontend assets, copied in at Docker build time — not in git
```

One structural rule: any future router must be registered in `create_app()` **before** the static-file mount — the mount matches every remaining path, so a route added after it would be unreachable. Noted inline in `main.py`.

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
| What's the product vision, phases, personas? | `docs/prds/nature-quest-prd-300726.md` |
| What happened in a past session? | `docs/status_docs/WORK_SUMMARY_*.md` (date order) |
| How was the pipeline concept validated? | `prototypes/README.md` |
| How do I run this locally / deploy it? | root `README.md`, `infra/README.md` |
