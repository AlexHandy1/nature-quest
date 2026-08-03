# ADR-001: Cloud platform, compute model & deployment topology

## Status
Accepted

## Date
2026-07-30

## Context
Nature Quest needs a first production deployment (PRD Slice 1 — production foundation): a "coming soon" landing page with a query-interest textbox, standing in for the eventual real pipeline. The goal of this slice is explicitly to prove the full deployment chain end-to-end (CI/CD → cloud deploy → monitoring → analytics), not to ship product features. The project has no existing production infrastructure — all prior work is throwaway prototypes (`prototypes/`) never intended for deployment.

## Decision
Deploy on **Google Cloud Platform**, using **Cloud Run** (serverless containers) as the compute model, with the application built as a **single monolithic service** — one Cloud Run service serving both the API and the built static frontend assets — rather than a microservices split.

## Alternatives Considered

### AWS (App Runner / ECS Fargate)
- Pros: comparable serverless-container story to Cloud Run.
- Cons: more verbose IaC for equivalent setups; no stated user familiarity with AWS.
- Rejected because: GCP was the user's stated preference and no AWS-specific advantage outweighed that.

### Vercel / Render / Fly.io
- Pros: faster to stand up, less IaC ceremony.
- Cons: weaker fit with the requirement for GCP-native monitoring/logging; doesn't build familiarity with the target cloud stack.
- Rejected because: doesn't serve the stated goal of proving the *real* GCP production path.

### GKE (Kubernetes)
- Pros: full orchestration control.
- Cons: real ongoing cost and operational overhead for a low-traffic "coming soon" page; no multi-service orchestration need exists yet.
- Rejected because: solves a scaling/orchestration problem the project doesn't have.

### Microservices (one Cloud Run service per pipeline stage)
- Pros: independent scaling/deployment per stage.
- Cons: real operational overhead (service discovery, inter-service auth, more services to monitor) for a solo-maintained project where pipeline stages are currently just sequential function calls, not services with independent scaling needs.
- Rejected because: over-engineering for current scale; the eventual real pipeline's stages don't yet need independent scaling.

## Consequences
- Near-zero idle cost (Cloud Run scales to zero) while the app is in "coming soon" / low-traffic state.
- Native integration with Cloud Logging, Cloud Monitoring, and Secret Manager, satisfying the monitoring/observability requirement without extra tooling.
- Cloud Run's up-to-60-minute request timeout leaves headroom for the eventual real pipeline's slower multi-stage LLM/GBIF calls.
- A single monolith service is simplest to deploy, monitor, and reason about now; splitting into separate frontend/backend deployments later (e.g. if the frontend needs independent CDN-level scaling) is a clean, well-understood refactor, not blocked by this decision.
- This decision does not lock in GCP forever, but a future provider migration would require redoing the IaC, CI/CD auth, and monitoring integration built on top of it.
