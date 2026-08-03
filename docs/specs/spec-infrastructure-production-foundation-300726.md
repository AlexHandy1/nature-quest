---
title: Production Foundation — Deployment, CI/CD, Security Baseline & Interest-Capture Landing Page
version: 1.0
date_created: 2026-07-30
last_updated: 2026-07-30
tags: [infrastructure, architecture]
status: Design complete, not yet built
sources: [docs/prds/nature-quest-prd-300726.md, docs/decisions/ADR-001-cloud-platform-compute-deployment-topology.md, docs/decisions/ADR-002-monorepo-structure.md, docs/decisions/ADR-003-frontend-stack-vite-react-typescript.md, docs/decisions/ADR-004-backend-framework-fastapi.md, docs/decisions/ADR-005-iac-terraform-cicd-github-actions.md, docs/decisions/ADR-006-secrets-environments-region-data-handling.md, docs/decisions/ADR-007-analytics-consent-abuse-guardrails.md, docs/decisions/ADR-008-open-source-from-day-one.md, docs/status_docs/WORK_SUMMARY_290726.md, prototypes/README.md]
---

# Introduction

This spec covers Nature Quest's **production foundation** (PRD Phase 1: `docs/prds/nature-quest-prd-300726.md`) — the first thing deployed to real infrastructure. It stands up the full production chain (monorepo → CI/CD → GCP Cloud Run → monitoring → analytics) end-to-end, fronted by a deliberately minimal "coming soon" landing page with an interest-capture form, rather than any part of the real NL-query pipeline (that pipeline is Phase 2, out of scope here — see §1). This document is meant to be implementable by an agent with no other context from this project's planning sessions — read it fully before writing code. All architectural decisions referenced here were made via an extensive interview process and are recorded in `docs/decisions/ADR-001` through `ADR-008`; treat them as settled facts to build from, not open questions, unless a section below explicitly flags something as `[NEEDS INPUT]`.

---

## 1. Purpose & Scope

**In scope:**
- A monorepo containing a FastAPI backend and a Vite + React + TypeScript frontend, deployed as one Cloud Run service (backend serves the built frontend's static assets).
- Terraform-provisioned GCP infrastructure (a new dedicated project, `europe-west1`), with remote state.
- GitHub Actions CI/CD: lint/type-check → unit tests → build/deploy → post-deploy smoke test, authenticated via Workload Identity Federation, designed from the start to run safely against pull requests from forks.
- GCP Secret Manager for runtime secrets (a PostHog project API key now; the codebase should not assume this is the only secret it will ever hold — see §4).
- A single public page: a "coming soon" landing page with one interest-capture form (free-text query only — no email or other PII collected by design), submitted to a backend endpoint that writes to structured logs and (where consent allows) to PostHog.
- Consent-gated PostHog analytics (EU-hosted), both client-side and server-side capture of the same submission event.
- Baseline anti-abuse protection on the public capture endpoint.
- Open-source project scaffolding: this spec does not choose a license (see §4, `[NEEDS INPUT]`) but does require the placeholder/process for adding one, plus a minimal `CONTRIBUTING.md`.

**Explicit non-goals for this slice:**
- No part of the real pipeline (NL query → GBIF species selection → route → narrative → map) is built or exposed here. The form's submitted text is stored as a raw interest signal only — it is never sent to an LLM or to GBIF in this slice.
- No staging environment. Single production environment only (ADR-006), but Terraform/CI are structured so adding one later is a config change, not a redesign.
- No custom domain. The app is reachable at its default Cloud Run URL (ADR-006).
- No database. Interest-capture submissions go to Cloud Logging only, plus PostHog (ADR-006) — no Firestore/Cloud SQL in this slice.
- No LLM-specific abuse/cost guardrails (prompt injection defenses, per-query LLM cost limits) — there is no LLM surface in this slice to protect. That work is scoped to when the real pipeline goes live (PRD Slice 2's LLM-facing portion).
- No license chosen — a real, considered legal decision outside this spec's scope (ADR-008, `[NEEDS INPUT]`).

---

## 2. Verified Facts

Everything below was decided in this project's architecture-review session and is not re-derived here — see the cited ADR for full reasoning and alternatives considered.

- **Cloud platform & compute**: GCP, Cloud Run (serverless containers), single monolithic service (backend serves built frontend assets). — ADR-001
- **Repo structure**: single monorepo containing production backend, production frontend, IaC, and CI/CD config, alongside the existing `prototypes/` (untouched, never deployed) and `docs/`. — ADR-002
- **Frontend**: Vite + React + TypeScript, full scaffold (including Vitest + React Testing Library) built now, not deferred. — ADR-003
- **Backend**: FastAPI, not Flask — chosen for its async model and native Pydantic integration (request/response validation, OpenAPI generation, and a path to generated TS types for the frontend). — ADR-004
- **IaC**: Terraform, with a **remote state backend** (a GCS bucket, IAM-controlled) rather than local state committed to the repo. This specific sub-decision (Terraform + remote state vs. an imperative `gcloud`-scripted approach) is flagged in ADR-005 as open to revisiting once real implementation experience exists — build against it as the current decision, but do not treat it as unquestionable if a technical spec for a later slice reopens it.
- **CI/CD**: GitHub Actions, authenticated via Workload Identity Federation (no static GCP keys in GitHub). Pipeline gate order: lint/type-check → unit tests (backend + frontend, run in parallel) → build/deploy → post-deploy smoke test. Must be safe to run against pull requests from forks from the first workflow written (ADR-005, ADR-008) — untrusted PR code must never gain access to deploy credentials or secrets.
- **Secrets**: GCP Secret Manager is the single source of truth for runtime secrets; Cloud Run reads secret values directly from Secret Manager at startup. GitHub Actions never sees secret values. — ADR-006
- **Environment/region**: one production environment (no staging yet), new dedicated GCP project, region `europe-west1`. — ADR-006
- **Interest-capture data**: free-text query and timestamp only — no email or other PII collected by design. Structured logs (Cloud Logging) only — no database. Also forwarded to PostHog. — ADR-006
- **Domain**: default `*.run.app` Cloud Run URL — no custom domain yet. — ADR-006
- **Analytics**: PostHog, EU-hosted Cloud instance, both client-side and server-side capture of the same key actions. — ADR-007
- **Consent**: PostHog initialized in a no-capture-by-default state (`opt_out_capturing_by_default: true`); no tracking identifier is set until the user actively opts in via the SDK's own opt-in call. A small custom consent banner (not a third-party consent-management platform) triggers this — PostHog's SDK owns the actual enforcement. — ADR-007
- **Abuse protection posture**: baseline automated-traffic mitigation and rate limiting on public-facing submission endpoints, proportionate to actual risk. No LLM-facing protection needed yet (no LLM surface exists in this slice). — ADR-007
- **Open source**: code and documentation are public from the first commit. No security control may depend on an attacker being unable to read its implementation — see §6 for how this shapes the abuse-protection mechanism chosen for this spec. — ADR-008
- **GBIF fetch-scaling issue** (55,756 occurrences / ~186 sequential page requests for one unfiltered query in prior prototyping) exists but is entirely out of scope for this slice — it only matters once the real pipeline is exposed (Phase 2+). — `docs/status_docs/WORK_SUMMARY_250726.md`, cited for context only, not actioned here.

---

## 3. Definitions

- **GCP**: Google Cloud Platform.
- **Cloud Run**: GCP's serverless container hosting product.
- **WIF**: Workload Identity Federation — lets GitHub Actions authenticate to GCP using short-lived tokens instead of a stored service account key.
- **ASGI**: Asynchronous Server Gateway Interface — the async server interface FastAPI runs on (via Uvicorn), as opposed to Flask's synchronous WSGI.
- **IaC**: Infrastructure as Code.
- **ADR**: Architecture Decision Record (`docs/decisions/`).
- **PostHog**: the product analytics tool used for this project (EU-hosted instance).
- **Consent-gated**: analytics capture that does not begin until the user has explicitly opted in.
- **Fork PR**: a pull request opened from a fork of this repository, i.e. by a contributor who does not have write access to it — the relevant threat model for CI/CD credential exposure.

---

## 4. Requirements, Constraints & Guidelines

**Repository & app structure**
- **REQ-001**: Production code lives in a monorepo alongside the existing `prototypes/` (untouched) and `docs/` directories. — ADR-002
- **REQ-002**: The backend is a FastAPI application; the frontend is a Vite + React + TypeScript application. — ADR-003, ADR-004
- **REQ-003**: The frontend's production build output is served by the backend as static files from the same Cloud Run service (no separate frontend deployment/CDN in this slice). — ADR-001, ADR-003

**Infrastructure**
- **REQ-004**: All GCP infrastructure is provisioned via Terraform, with state in a remote GCS backend — never committed to the repo. — ADR-005
- **REQ-005**: A new, dedicated GCP project is created for this application; region `europe-west1` for all regional resources. — ADR-006
- **REQ-006**: Exactly one environment (production) is provisioned in this slice. Environment name must be a Terraform variable, not hardcoded, so a second environment can be added later without restructuring. — ADR-006
- **CON-001**: No custom domain is configured. The app is reachable only at its Cloud Run-assigned URL. — ADR-006

**Secrets**
- **REQ-007**: All runtime secrets (starting with the PostHog project API key) are stored in GCP Secret Manager and injected into the Cloud Run service at startup — never as plain environment variables set via Terraform/CI, never committed to the repo. — ADR-006
- **CON-002**: GitHub Actions must never have direct read access to secret values — only enough IAM permission to trigger a deploy where Cloud Run itself pulls secrets from Secret Manager. — ADR-005, ADR-006

**CI/CD**
- **REQ-008**: The GitHub Actions pipeline runs, in order: lint/type-check (backend: a Python linter/type-checker; frontend: ESLint + `tsc --noEmit`) → unit tests (backend `pytest`, frontend Vitest, run in parallel) → build (Docker multi-stage: frontend build stage, then backend image) → deploy to Cloud Run → post-deploy smoke test against the live URL. A failure at any stage halts the pipeline before the next stage runs. — ADR-005
- **REQ-009**: Authentication from GitHub Actions to GCP uses Workload Identity Federation exclusively. No GCP service account key is stored as a GitHub secret. — ADR-005
- **REQ-010**: Any workflow that can be triggered by a pull request from a fork must run with no access to deploy credentials or repository secrets. Deploy/infra-provisioning workflows must only run on pushes to the trunk branch (or an explicitly protected branch), never on `pull_request` events from forks, and must not use `pull_request_target` to run untrusted PR code with elevated permissions. — ADR-005, ADR-008
- **REQ-011**: CI triggers use path-based filters so unrelated changes (e.g. a `docs/`-only commit) don't rebuild/redeploy the application. — ADR-002

**Interest-capture endpoint**
- **REQ-012**: A `POST` endpoint accepts a free-text `query` (required, and the only submitted field — no email or other PII is collected by design) and writes a structured log entry (Cloud Logging) recording the submission — no database. — ADR-006
- **REQ-013 `[DEFERRED]`**: The same submission triggers a PostHog event, captured server-side, **only when the request indicates the user has already consented client-side** (see REQ-016) — the server must not unconditionally mirror an event that bypasses the user's consent choice. **Deferred during implementation (2026-08-03)**: server-side PostHog capture requires a `distinct_id`, and correctly pairing it with the client's own anonymous PostHog ID (so both events attribute to the same visitor) needs a new field on the request contract — judged excessive for getting the basics running; client-side capture alone is used for now. Revisit alongside REQ-016 once dual-channel capture is actually needed. Consent-gating itself (REQ-014/015) is unaffected by this deferral — it lives entirely in the client-side SDK and doesn't depend on a server-side call existing.
- **GUD-001**: Treat writing the submission to Cloud Logging (the interest signal itself) as necessary processing for the feature the user explicitly requested (joining an interest list), distinct from the PostHog *behavioral tracking* consent gate. No email, name, or other directly identifying field is collected at any point in this flow — the working position on privacy scope for this project is deliberate caution based on own research rather than formal legal review, which isn't proportionate at this project's current scale.

**Analytics & consent**
- **REQ-014**: PostHog is initialized client-side with `opt_out_capturing_by_default: true`. No PostHog identifier/cookie is set and no event is sent until the user opts in via a visible consent banner. — ADR-007
- **REQ-015**: The consent banner is custom-built (not a third-party consent-management platform), presenting an accept/reject choice that calls PostHog's own `opt_in_capturing()` / `opt_out_capturing()`. — ADR-007
- **REQ-016 `[DEFERRED]`**: The frontend includes an explicit consent flag in its `POST` request to the interest-capture endpoint, reflecting the current PostHog opt-in state at submission time, so the backend can honor REQ-013 without needing its own separate consent store. **Deferred alongside REQ-013** — not sent or accepted in the current build; `InterestSubmission` currently has only `query`. When server-side capture is built, the payload should carry the client's PostHog `distinct_id` (already anonymous, no new PII) rather than reinventing one server-side, so the client and server events attribute to the same visitor.
- **REQ-016b**: The consent banner includes a short, plain-language disclosure (1-2 lines, no legal jargon) of what is and isn't collected — see the proposed copy in §11. This is a deliberately simple, self-drafted disclosure appropriate to this project's current scale, not formally reviewed legal copy.

**Abuse protection**
- **REQ-017**: The interest-capture endpoint has GCP Cloud Armor rate limiting applied at the load-balancer/edge level, as the primary, fully-specified anti-abuse control (see §6 for the mechanism and why it's named explicitly here rather than treated as security-sensitive). — ADR-007, ADR-008
- **GUD-002**: Any additional, lighter-weight anti-automation signal on the submission form itself is left as a build-time implementation detail rather than specified in this document, consistent with this project's rule against publishing exploitable specifics of a control whose value depends partly on not being predictable — Cloud Armor (REQ-017) is the load-bearing, disclosure-safe control; this is supplementary defense-in-depth only, not something this spec's acceptance criteria depend on.

**Open-source scaffolding**
- **REQ-018**: A minimal placeholder `CONTRIBUTING.md` exists at the repo root — boilerplate noting the project is public but not yet actively seeking contributions or shares at this stage, with a short "how to run this locally" pointer. Not a full contribution guide; that's a later exercise once the project is actually ready to onboard outside contributors.
- **CON-004**: A LICENSE file is a known, tracked gap, not resolved by this spec — `[NEEDS INPUT: license choice, PRD Dependencies & Blockers]`. Do not guess a license; leave this as an explicit open task.

**Testing (production code — full TDD applies, not the prototype-only light convention)**
- **REQ-019**: Backend test coverage (via `pytest`) includes: request validation for the interest-capture endpoint (valid/invalid `query`), and the structured-log write path. The consent-gated server-side PostHog call is deferred along with REQ-013/REQ-016 — not currently tested or implemented.
- **REQ-020**: Frontend test coverage (via Vitest + React Testing Library) includes: the landing page renders the form and copy correctly, form submission calls the backend endpoint with the right payload shape, success/error states render correctly, and the consent banner correctly gates PostHog initialization and persists the user's choice.
- **REQ-021**: A post-deploy smoke test (run by CI after every deploy, against the live Cloud Run URL) confirms: a health-check endpoint returns 200, and a real form submission against the live deployed service succeeds end-to-end. — ADR-005

---

## 5. Interfaces & Data Contracts

### `GET /healthz`
Liveness/readiness check used by the post-deploy smoke test (REQ-021).

**Response `200`**:
```json
{ "status": "ok" }
```

### `POST /api/interest`
The interest-capture submission endpoint (REQ-012; REQ-013/REQ-016 deferred — see below).

**Request body** (Pydantic model, validated per ADR-004's FastAPI/Pydantic integration):
```python
class InterestSubmission(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
```

No email or other directly identifying field is accepted here by design — see §1/REQ-012.

`analytics_consent` (REQ-016) is deferred along with server-side PostHog capture (REQ-013) — not part of the current request contract. When reintroduced, the payload should carry the client's PostHog `distinct_id` instead of a plain boolean, so the deferred server-side event attributes to the same anonymous visitor as the client-side event.

**Response `201`**:
```json
{ "status": "received" }
```

**Response `422`**: FastAPI's standard validation-error shape (auto-generated from the Pydantic model above) when `query` is missing or empty.

**Response `429`**: returned by the Cloud Armor rate-limiting layer (REQ-017) when a client exceeds the configured threshold — this response is generated at the infrastructure layer, not application code, so the exact shape follows GCP's default Cloud Armor response unless a custom response is configured.

### PostHog events

| Event | Trigger | Fired from |
|---|---|---|
| `landing_page_viewed` | Page load, only after consent | Client-side |
| `interest_form_submitted` `[DEFERRED]` | Successful `POST /api/interest`, only when `analytics_consent: true` was sent | Server-side (REQ-013, deferred) |

---

## 6. Implementation Mechanics

### File layout (monorepo)

```
/                          # repo root
  README.md
  CLAUDE.md
  CONTRIBUTING.md          # REQ-018
  LICENSE                  # NEEDS INPUT — placeholder until chosen
  app/
    backend/               # FastAPI app
      main.py
      routers/
        interest.py        # POST /api/interest, GET /healthz
      models/
        interest.py        # InterestSubmission Pydantic model
      services/
        posthog_client.py  # server-side PostHog capture (REQ-013)
        logging_client.py  # structured Cloud Logging writer (REQ-012)
      tests/
      Dockerfile
      pyproject.toml / requirements.txt
    frontend/               # Vite + React + TypeScript app
      src/
        App.tsx
        components/
          InterestForm.tsx
          ConsentBanner.tsx
        lib/
          posthog.ts        # client-side PostHog init, consent-gated (REQ-014, REQ-015)
      tests/
      vite.config.ts
      package.json
  infra/                    # Terraform
    main.tf
    backend.tf              # remote state config (REQ-004)
    variables.tf            # includes environment name (REQ-006)
    cloud_run.tf
    secret_manager.tf
    cloud_armor.tf           # REQ-017
  .github/
    workflows/
      ci-cd.yml              # REQ-008–REQ-011
  prototypes/                # unchanged, untouched, never deployed
  docs/                      # unchanged structure
```

### Docker build

Multi-stage build: a Node build stage (`npm ci && npm run build` in `app/frontend/`) producing static assets, copied into the final Python image alongside the FastAPI app, which serves them directly (ADR-001's monolith decision, ADR-003's frontend-build consequence). One Cloud Run service, one image.

### Backend concurrency

Per ADR-004's stated porting requirement: use `httpx` (async) rather than `requests` for the server-side PostHog HTTP call, and ensure no blocking calls sit unwrapped inside `async def` request handlers. This slice has no GBIF/Anthropic calls yet, so the surface area for this requirement is small — but the pattern (async-compatible clients, or `run_in_threadpool` for anything that isn't) must be established correctly here, since Phase 2 will build directly on top of it.

### Abuse protection mechanism (REQ-017)

Cloud Armor's rate-limiting policy is applied to the load balancer in front of the Cloud Run service, throttling requests to `POST /api/interest` per client IP. This is specified explicitly (rather than treated as sensitive, unlike the ADR-007 abuse-posture language) because — per ADR-008's own principle — a control whose effectiveness depends on an attacker not knowing it exists isn't a real control; Cloud Armor rate limiting remains effective even when its existence and general mechanism (GCP edge-level, per-IP request throttling) are fully public, the same way a public API openly documenting its rate limits doesn't weaken them. The exact threshold is a tunable Terraform variable, not hardcoded here — start conservative and adjust based on real traffic once observed (see §12).

### Data flow for a single submission

1. User fills the form, optionally accepts the consent banner (REQ-014/015).
2. Frontend `POST`s to `/api/interest` with `{query, analytics_consent}` (REQ-016).
3. Cloud Armor rate limiting evaluates the request first (REQ-017); if over threshold, `429` before the request reaches the application.
4. FastAPI validates the payload (Pydantic); `422` on failure.
5. Backend writes a structured Cloud Logging entry (REQ-012).
6. If `analytics_consent: true`, backend fires the `interest_form_submitted` PostHog event server-side (REQ-013).
7. Backend returns `201`.
8. Frontend shows a success state.

---

## 7. Acceptance Criteria

- **AC-001**: Given a fresh clone of the repo, when `docker build` is run against the backend image, then the built image contains the compiled frontend static assets and serves them at the app's root route.
- **AC-002**: Given a push to the trunk branch, when the GitHub Actions pipeline runs, then it executes lint/type-check, unit tests, build, deploy, and a post-deploy smoke test in that order, and a failure at any stage prevents the next stage from running.
- **AC-003**: Given a pull request opened from a fork, when its CI workflow runs, then it has no access to any repository secret or deploy credential, and cannot trigger a deploy.
- **AC-004**: Given the deployed app with no user interaction yet, when Cloud Logging or PostHog is inspected, then no interest-capture events exist for that visitor (nothing fires on page load before consent).
- **AC-005 `[DEFERRED — depends on REQ-013]`**: Given a user who accepts the consent banner and submits the form, when the submission completes, then a structured log entry exists (REQ-012) **and** a PostHog `interest_form_submitted` event exists for that submission.
- **AC-006 `[DEFERRED — depends on REQ-013]`**: Given a user who rejects the consent banner and submits the form, when the submission completes, then a structured log entry exists (REQ-012) **but no** PostHog event fires for that submission.
- **AC-007**: Given a client sending requests to `POST /api/interest` above the configured Cloud Armor threshold, when the threshold is exceeded, then subsequent requests receive `429` before reaching application code.
- **AC-008**: Given a request to `/healthz`, when called, then it returns `200 {"status": "ok"}` without requiring any secret or external dependency to be healthy.
- **AC-009**: Given the repo at rest, when searched for Terraform state files or plaintext secret values, then none are found committed anywhere in git history for this slice.

---

## 8. Test Strategy

Full TDD per `/tdd` and `/testing` — this is production code, so the project's light/deterministic-logic-only prototype testing convention does not apply.

**Backend (`pytest`)**:
- Unit: `InterestSubmission` validation (empty `query` rejected, oversized `query` rejected, valid payload accepted).
- Unit: the structured-log writer is called with the expected fields on every valid submission.
- Integration: `POST /api/interest` via FastAPI's `TestClient`, covering the `201`/`422` response shapes end-to-end through the real routing/validation layer.
- `[DEFERRED]` Unit: the server-side PostHog capture call fires if and only if `analytics_consent: true` is present in the validated request (REQ-013) — assert via a mocked PostHog client, not a real network call. Add this back once REQ-013/016 are built.

**Frontend (Vitest + React Testing Library)**:
- `InterestForm` renders the landing copy and form fields; submitting calls the expected endpoint with the expected payload shape; success and error states render correctly.
- `ConsentBanner` renders on first visit, calls `posthog.opt_in_capturing()`/`opt_out_capturing()` on the respective button, and does not re-render on subsequent visits once a choice has been persisted.
- The PostHog client wrapper (`lib/posthog.ts`) is initialized with `opt_out_capturing_by_default: true` and does not fire any capture call before consent is granted (mock the underlying SDK, don't hit real PostHog in tests).

**CI-level (not unit tests, but part of the pipeline — REQ-021)**:
- Post-deploy smoke test: `GET /healthz` returns `200`; a real `POST /api/interest` against the live deployed URL returns `201` and is observable in Cloud Logging shortly after.

**Not tested here** (explicitly out of scope for this slice, consistent with §1): Cloud Armor's actual rate-limiting behavior under load is an infrastructure-level control, not something exercised by the application test suite — validate it manually per §12, not via CI.

---

## 9. Rationale & Context

Every major choice in this spec (cloud/compute, repo structure, frontend/backend stack, IaC/CI-CD tooling, secrets/environment/region, analytics/consent posture) was resolved through an extensive architecture-review interview and is recorded with full alternatives-considered reasoning in `docs/decisions/ADR-001` through `ADR-007`; this spec does not re-justify those choices, only turns them into buildable detail. `ADR-008` (open-source from day one) is the one decision whose consequences required new, spec-level design work beyond what the ADRs alone specify — specifically REQ-010 (fork-PR CI safety) and the Cloud Armor framing in §6, since "how do you write an implementable spec for an abuse-protection mechanism without publishing exploitable detail in a public repo" was a real tension this spec had to resolve, not just record. The resolution — specify controls whose effectiveness doesn't depend on secrecy (Cloud Armor) in full, and leave secrecy-dependent supplementary layers as an unspecified build-time detail (GUD-002) — follows directly from ADR-008's own stated principle rather than inventing a new one.

---

## 10. Dependencies & External Integrations

- **GCP**: Cloud Run, Secret Manager, Cloud Logging, Cloud Monitoring, Cloud Armor, IAM, Artifact Registry (container image storage), GCS (Terraform remote state).
- **GitHub Actions**: CI/CD runner, Workload Identity Federation for GCP auth.
- **PostHog**: EU Cloud instance — both a client-side JS SDK dependency and a server-side Python client dependency.
- **Terraform**: GCP provider.
- **Frontend tooling**: Vite, React, TypeScript, Vitest, React Testing Library.
- **Backend tooling**: FastAPI, Pydantic, Uvicorn/Gunicorn, `httpx`, `pytest`.

---

## 11. Examples & Edge Cases

### Landing page copy

Proposed, not locked — content like this is low-cost to revise later and isn't an architectural decision:

- **Heading**: "Nature Quest"
- **Tagline**: "Coming soon — an AI agent that turns requests like 'show me some plants and birds here' into personalised, narrated nature walks grounded in real biodiversity data."
- **Form label**: "What would you want to see on a walk?"
- **Input placeholder**: `e.g. "show me some birds near here" or "something rare"`
- **Submit button**: "Count me in"

The label deliberately stays short and open, while the placeholder text primes the same free-text query format the real product will eventually use — so early submissions double as a real signal of what people actually ask for, not just generic "notify me" interest.

### Consent banner disclosure copy (REQ-016b)

Proposed, plain-language, 1-2 lines — not formally reviewed legal copy, just a clear, honest statement of what is and isn't collected:

> "We use privacy-friendly analytics to see how people use this page. We never ask for or store your name or email."

### Edge cases

- **Empty/whitespace-only `query`**: rejected by Pydantic's `min_length=1` (REQ-012's model) — `422`, not silently accepted as an empty interest signal.
- **User submits the form twice in quick succession**: no deduplication logic in this slice — each submission is logged independently; deduplication is not a requirement here (out of scope, not flagged as a gap since duplicate "I'm interested" signals aren't harmful at this stage).
- **User rejects consent, then later re-visits and accepts it**: the consent banner must not re-prompt once a choice is persisted (REQ-020's test coverage), but changing the choice later is a legitimate PostHog SDK capability — not specifically built out in this slice beyond what the SDK provides by default.
- **A fork PR modifies `.github/workflows/ci-cd.yml` itself**: per REQ-010, this must not grant that PR's run any secret access — this is the specific scenario REQ-010 exists to prevent, and should be the concrete case validated in §12.

---

## 12. Validation Criteria

- Run `docker build` locally against the backend image; confirm the frontend's built assets are present and served.
- Push to trunk, watch the GitHub Actions run complete all five pipeline stages (REQ-008) in order.
- Open a PR from a fork (or simulate one) that attempts to read a repository secret from within its workflow run; confirm it fails/has no access (AC-003).
- Visit the deployed URL fresh (no prior cookies); confirm via browser devtools that no PostHog network call fires before the consent banner is answered.
- Accept consent, submit the form; confirm both a Cloud Logging entry and a PostHog `interest_form_submitted` event exist for that submission (AC-005).
- Reject consent, submit the form; confirm a Cloud Logging entry exists but no PostHog event does (AC-006).
- Send requests past the configured Cloud Armor threshold; confirm `429` responses begin (AC-007) — do this against a non-production-critical test window, since it will generate log noise.
- `grep` the full git history for `.tfstate` or known secret-value patterns; confirm nothing is found (AC-009).

---

## 13. Related Specs / Further Reading

- Parent PRD: `docs/prds/nature-quest-prd-300726.md` (Phase 1: Production Foundation).
- `docs/decisions/ADR-001` through `ADR-008` — full reasoning and alternatives for every major choice this spec builds on.
- `docs/status_docs/WORK_SUMMARY_290726.md` — the session that first flagged production foundation as the top priority.
- No other specs exist yet in `docs/specs/` — this is the first. Future specs for Phase 2 (the real NL-query pipeline) should treat this spec's file layout (§6) and testing conventions (§8) as the established pattern to extend, not redesign.
