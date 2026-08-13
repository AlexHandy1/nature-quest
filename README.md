# Nature Quest

Turn public biodiversity data into navigable nature walks.

Given a location and a natural-language request (e.g. "show me some birds", "I want to see fish and insects"), Nature Quest dynamically selects the species currently present that match, designs a guided walk route through their recorded sighting hotspots, and generates a narrated field guide grounded in real observation data.

**Live**: https://nature-quest-production-465dsuxpnq-ew.a.run.app — draw a search area **anywhere in the world** on the map (or use the default Retiro Park, Madrid area), then submit a free-text request (e.g. "show me some birds") to get back a real species list and walking route for that area.

## Running it locally

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

## Deploying

Merging to `main` deploys automatically via GitHub Actions (Workload Identity Federation, no static keys — see `docs/decisions/ADR-005`). For one-off manual deploys before/outside CI, see `infra/README.md`.

## Finding your way around

- **`ARCHITECTURE.md`** — the current, whole-system map: components, request flow, deploy flow, what's live vs. deferred. Start here.
- **`docs/decisions/`** — ADRs, the "why" behind significant technical decisions.
- **`docs/specs/`** — technical specs for each slice of work, precise enough to build from.
- **`docs/prds/`** — product requirements: vision, phases, personas.
- **`docs/status_docs/`** — session-by-session notes (`WORK_SUMMARY_*.md`, `DDMMYY` date order).
- **`prototypes/`** — throwaway code that validated the original pipeline concept (species selection → waypoint ordering → enrichment/narrative → presentation) before any production code was written. Untouched, never deployed, not part of the current architecture — see `prototypes/README.md`.

## Open source

Nature Quest is built in the open from its first commit — code and documentation are public from the outset, so other developers can contribute or fork and rebuild. See `docs/decisions/ADR-008-open-source-from-day-one.md` for what that implies for security posture and CI/CD design.

A LICENSE has not been chosen yet — until it is, the terms under which this code may be used, forked, or contributed to are undefined.
