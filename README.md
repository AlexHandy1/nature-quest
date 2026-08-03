# Nature Quest

Turn public biodiversity data into navigable nature walks.

Given a location and a natural-language request (e.g. "show me some birds", "something rare"), Nature Quest dynamically selects the species currently present that match, designs a guided walk route through their recorded sighting hotspots, and generates a narrated field guide grounded in real observation data.

**Live (coming-soon landing page + interest capture)**: https://nature-quest-production-465dsuxpnq-ew.a.run.app

## Current state

The real NL-query pipeline (species → route → narrative → map) is not built yet — it was validated end-to-end as a throwaway prototype (see below) and is Phase 2 of the plan. What's live now is Phase 1: the **production foundation** — a "coming soon" landing page with a consent-gated interest-capture form, deployed on real infrastructure with a full CI/CD pipeline, per `docs/specs/spec-infrastructure-production-foundation-300726.md`.

- **`app/backend/`** — FastAPI backend: `GET /health`, `POST /api/interest` (validates and structured-logs a free-text interest signal, no PII), serves the built frontend as static files.
- **`app/frontend/`** — Vite + React + TypeScript: landing page, interest form, a custom consent banner gating client-side PostHog analytics.
- **`infra/`** — Terraform-provisioned GCP infrastructure (Cloud Run, Artifact Registry, Secret Manager scaffold, Workload Identity Federation for CI/CD). See `infra/README.md` for one-time bootstrap/manual-deploy steps.
- **`.github/workflows/ci-cd.yml`** — lint/type-check → unit tests → build → deploy → post-deploy smoke test, on every push to `main`; PRs get lint/test/build only, no deploy credentials.

### Running it locally

Two servers, in separate terminals:

```bash
# backend
cd app/backend
python -m venv venv && source venv/bin/activate   # first time only
pip install -r requirements-dev.txt                # first time only
uvicorn main:app --reload --port 8000

# frontend
cd app/frontend
npm install       # first time only
npm run dev
```

The frontend dev server proxies `/api` and `/health` to the backend (`vite.config.ts`), so both run together as one app at `http://localhost:5173`.

### Deploying

Merging to `main` deploys automatically via GitHub Actions (Workload Identity Federation, no static keys — see `docs/decisions/ADR-005`). For one-off manual deploys before/outside CI, see `infra/README.md`.

## Prototype validation (historical)

Before any production code was written, the full pipeline concept was validated as a throwaway prototype — not shipped, not maintained, kept only as a reference for what was proven to work:

1. **Species selection** — a natural-language walk request (e.g. "show me some birds") turned into a structured GBIF query by an LLM, resolved to real GBIF taxonomy keys, fetching real occurrence records for a location.
2. **Waypoint ordering** — observation hotspots ordered into a walkable route (nearest-neighbour from a start point).
3. **Enrichment + narrative** — each species gets a GBIF common name, a Wikipedia-sourced description, and a generated narrative guide.
4. **Presentation** — a game-like ("adventure-style quest log") map/journal UI.

Proven end-to-end on `claude-haiku-4-5-20251001`, chosen after cost/time experiments showed no visible quality loss vs. Sonnet 5 for these call shapes. See `prototypes/README.md` for what was built, why, and how to run it — that code is untouched and never deployed.

## Planning docs

See `docs/` for PRDs (`docs/prds/`), technical specs (`docs/specs/`), architecture decision records (`docs/decisions/`), and session-by-session notes (`docs/status_docs/`, `WORK_SUMMARY_*.md` in `DDMMYY` date order).

## Open source

Nature Quest is built in the open from its first commit — code and documentation are public from the outset, so other developers can contribute or fork and rebuild. See `docs/decisions/ADR-008-open-source-from-day-one.md` for what that implies for security posture and CI/CD design.

A LICENSE has not been chosen yet — until it is, the terms under which this code may be used, forked, or contributed to are undefined.
