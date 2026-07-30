# ADR-003: Frontend stack — Vite + React + TypeScript

## Status
Accepted

## Date
2026-07-30

## Context
All prior frontend prototyping (`prototypes/web/*.html`) uses plain, unbundled, hand-rolled JS/CSS inlined into single HTML files, per this project's "prototypes stay standalone" convention (small helpers duplicated across files rather than shared). This has already caused a real production-relevant bug: a raw-occurrence-points feature was fixed in one prototype's JS but not in `index_full_validation.html`'s separately-duplicated `renderMapView()`, because the logic existed in two places with no shared source of truth (`docs/status_docs/WORK_SUMMARY_290726.md`). Production frontend code needs a decision on whether to carry the same hand-rolled approach forward or introduce a framework and build toolchain.

## Decision
Build the production frontend with **Vite + React + TypeScript**, including its full scaffold, test setup (Vitest + React Testing Library), and CI build step, starting now in this production-foundation slice — even though the initial "coming soon" landing page only needs a heading and a form.

## Alternatives Considered

### Plain HTML/JS (continue the prototype pattern)
- Pros: zero build toolchain, zero new tooling to learn, matches everything validated in prototyping — ten rounds of fairly complex client-side interactivity (Leaflet map, draw-your-own-polygon, modals, state-swapped views) were built this way with no framework-related pain reported.
- Cons: no component reuse across near-identical flows — the exact failure mode that caused the raw-occurrence-points bug; no type safety; no serious testing story for frontend logic beyond manual/visual checks.
- Rejected because: the componentization/reuse problem is a real, observed correctness liability once there's shared UI logic across multiple production flows, not just a throwaway-prototype convenience trade-off.

### Defer the framework to Phase 2 (ship Phase 1 as plain HTML, introduce React when real UI complexity arrives)
- Pros: less upfront work for a trivial landing page.
- Cons: this slice's whole purpose is proving the *real* production deployment pipeline (Docker, CI/CD, IaC) end-to-end; building a throwaway static page now means redoing the Docker multi-stage build, CI steps, and static-asset IaC wiring when React arrives in Phase 2 anyway.
- Rejected because: a low-stakes landing page is the ideal place to shake out the real build pipeline, not the place to fake it.

## Consequences
- New CI step required: `npm ci` / `npm run build` / type-check / lint, before the Docker build stage.
- Docker build becomes multi-stage: a Node build stage producing static assets, copied into the final image served by the backend (see ADR-001's monolith decision — assets are baked into the same Cloud Run service, not split to a CDN).
- Production frontend has a real test suite (Vitest + React Testing Library) from day one, satisfying this project's TDD requirement for production code (as distinct from the prototype-only "light TDD, deterministic logic only" convention).
- Enables downstream benefits realized in ADR-004 (FastAPI's auto-generated OpenAPI schema → generated TypeScript types keeping the API contract in sync with the frontend by construction).
- Real, non-hypothetical maintenance cost: a Node/npm toolchain, dependency updates, and build configuration to maintain going forward, for a solo maintainer.
