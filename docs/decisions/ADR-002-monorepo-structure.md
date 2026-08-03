# ADR-002: Monorepo structure for production code

## Status
Accepted

## Date
2026-07-30

## Context
Production code (backend, frontend, infrastructure-as-code, CI/CD config) needs a home in version control, alongside the existing `prototypes/` directory (which stays undeployed, per this project's established "prototypes stay standalone" convention) and `docs/` (planning, PRDs, specs, ADRs). The project has a single, solo maintainer.

## Decision
Use a **single monorepo** — one Git repository containing production backend, production frontend, infrastructure-as-code, and CI/CD configuration, alongside the existing `prototypes/` and `docs/` directories.

## Alternatives Considered

### Polyrepo (separate repos per component)
- Pros: clean ownership/release-cadence boundaries when multiple independent teams are involved.
- Cons: for a solo maintainer, buys no real ownership-boundary benefit; cross-cutting changes (e.g. a backend API change and its frontend consumer) require coordinating commits/PRs across repos instead of landing atomically in one.
- Rejected because: no team-ownership boundary exists to protect, and the coordination overhead is pure cost with no offsetting benefit at this project's scale.

## Consequences
- Cross-cutting changes (API contract + frontend consumer, infra + app code) land atomically in one commit/PR.
- One CI/CD pipeline and one Terraform state story to reason about.
- CI must use path-based triggers (e.g. GitHub Actions `paths:` filters) so a change in one part of the repo (e.g. `docs/`) doesn't trigger unnecessary rebuilds/redeploys of unrelated services (e.g. the backend).
- If the project ever needs genuinely independent release cadences or team ownership boundaries, splitting the monorepo later is possible but not free — this decision is easy to keep, harder to reverse cleanly once history and tooling assume one repo.
