# ADR-008: Open-source from day one

## Status
Accepted

## Date
2026-07-30

## Context
The project is being built as open source from its very first commit — both code and documentation are public, not made public later once mature. This is a deliberate choice to give other developers the opportunity to contribute or fork and rebuild. This has implications beyond the individual technology choices already recorded in ADR-001 through ADR-007, since those decisions were made without this constraint made explicit.

## Decision
Build and document Nature Quest fully in the open from the outset. This carries several concrete consequences for how other decisions in this project must be executed, not just documented:

1. **No security control may depend on obscurity.** Anything that would stop working once an attacker can read the exact implementation (not just know a control exists) is not a sufficient control on its own. This sharpens ADR-007's abuse-protection posture: mechanisms must remain effective when the code implementing them is fully public.
2. **CI/CD must be designed for eventual external contributions from the start**, not retrofitted once the first outside pull request arrives. GitHub Actions workflows that run on pull requests from forks are a known credential-exfiltration risk if misconfigured (e.g. via `pull_request_target` misuse) — this must inform ADR-005's CI/CD design.
3. **Terraform's state must use a remote backend, not local state.** This is a consequence of the tool already chosen in ADR-005, not a general open-source principle in itself — Terraform is one of several viable IaC approaches, and specifically maintains a persistent state artifact that other approaches (e.g. imperative `gcloud` scripts, where the cloud itself is the only source of truth) don't. Having chosen Terraform, a remote backend keeps that artifact out of the repo and IAM-controlled regardless of the repo's own visibility, while preserving Terraform's plan/diff review value — genuinely useful for a project where external contributors may propose infrastructure changes. This trade-off (Terraform + remote backend vs. a simpler scripted approach) may be revisited once real implementation experience with either surfaces new considerations.
4. **A LICENSE and basic contribution scaffolding (at minimum, `CONTRIBUTING.md`) are required project setup items**, not optional polish, since the explicit intent is for others to fork/contribute. The specific license choice is a separate decision, not made by this ADR — [NEEDS INPUT].

## Alternatives Considered

### Private repository, open-sourced later once mature
- Pros: avoids the above constraints during early, messier iteration; a "reveal" moment can be more polished.
- Cons: directly contradicts the stated goal of giving other developers the opportunity to contribute or fork from early on; retrofitting security-not-through-obscurity and CI hardening onto an already-built private codebase is more work than designing for it from the start.
- Rejected because: explicit user intent — the project is meant to be public and forkable from day one, not after the fact.

## Consequences
- Every future ADR and technical spec must be written assuming the reader could be an unknown external contributor or an adversary, not just a future version of the current team.
- Slice 2 (security & abuse guardrails) is scoped more strictly than it would be for a private codebase — see ADR-007.
- Slice 1 (production foundation) must include a remote Terraform state backend and fork-aware CI/CD design from its first implementation, not as a fast-follow.
- A license decision is now a blocking open item for the project (see PRD Dependencies & Blockers) — until resolved, the terms under which others may legally use, fork, or contribute to the code are undefined.
