# Nature Quest

Turn public biodiversity data into navigable nature walks.

Given a location and a natural-language request (e.g. "show me some birds", "something rare"), Nature Quest dynamically selects the species currently present that match, designs a guided walk route through their recorded sighting hotspots, and generates a narrated field guide grounded in real observation data.

**Live**: https://nature-quest-production-465dsuxpnq-ew.a.run.app — submit a free-text request (e.g. "show me some birds") against Retiro Park, Madrid, or draw your own search area anywhere on the map.

## Current state

Phase 1 (production foundation) and Slice 2 (LLM guardrails + a first real GBIF query) are both **built and deployed**. The landing page's primary interaction is `POST /api/query`: free text → a real Anthropic call resolves it to a GBIF taxon → a real GBIF species list comes back — behind per-IP rate limiting, a daily LLM-call budget, structured operational logging, and consent-gated PostHog observability (client- and server-side). The Anthropic API key is fetched explicitly from Secret Manager at container startup, never a plain env var in production. See `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md` and `ARCHITECTURE.md` for full detail.

Still outstanding from Slice 2's scope: basic GCP uptime/error-rate alerting (REQ-025-027). A real-time email alert on every query submission was built first instead — a deliberate short-term trade-off, not a scalable monitoring posture; see `docs/decisions/ADR-010-realtime-per-query-alerting.md`.

Slice 3 adds density-cluster hotspots and nearest-neighbour route ordering, plus a map UI (`MapView`, react-leaflet) with numbered markers and a route line — species enrichment (narrative, descriptions) is later PRD slices, not built yet. It was validated end-to-end as a throwaway prototype (see below) before being ported to production.

Slice 9 adds draw-your-own-area: choose the fixed Retiro Park area or draw a custom polygon anywhere, via an always-visible area-selection widget (`AreaControl`) rather than a one-time setup step — see `docs/decisions/ADR-012-area-selection-persistent-widget-not-popup-funnel.md` for how this diverged from its original spec during implementation. `/api/query`'s `polygon` field is required; there's no backend default area.

- **`app/backend/`** — FastAPI backend: `GET /health`, `POST /api/query` (Slice 2, extended in Slices 3 and 9), serves the built frontend as static files.
- **`app/frontend/`** — Vite + React + TypeScript: the map landing page (`MapView`), a persistent area-selection widget (`AreaControl`) and draw-your-own-area tool (`DrawAreaControl`), an inline query form (`QueryPanel`), a results list (`ResultsPanel`), a custom consent banner gating client-side PostHog analytics.
- **`infra/`** — Terraform-provisioned GCP infrastructure (Cloud Run, Artifact Registry, Secret Manager, Workload Identity Federation for CI/CD). See `infra/README.md` for one-time bootstrap/`terraform apply`/manual-deploy steps.
- **`.github/workflows/ci-cd.yml`** — lint/type-check → unit tests → build → deploy → post-deploy smoke test, on every push to `main`; PRs get lint/test/build only, no deploy credentials. `infra/manual_deploy.sh` reproduces this locally if GitHub Actions itself is unavailable.

### Running it locally

Two servers, in separate terminals:

```bash
# backend — POST /api/query needs ANTHROPIC_API_KEY in a repo-root .env (gitignored);
# add POSTHOG_PROJECT_TOKEN too if testing server-side PostHog capture (consent=True)
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
