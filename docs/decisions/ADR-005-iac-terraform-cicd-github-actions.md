# ADR-005: Infrastructure-as-code (Terraform) & CI/CD (GitHub Actions)

## Status
Accepted

## Date
2026-07-30

## Context
The project needs reproducible, version-controlled infrastructure provisioning on GCP (ADR-001), and a CI/CD pipeline that deploys from the existing GitHub-hosted monorepo (ADR-002), gated on testing. The repo is public from day one (ADR-008), which bears on both the state-storage approach and the CI/CD design below.

## Decision
Provision infrastructure with **Terraform**, using a **remote state backend** (a GCS bucket, access-controlled via IAM) rather than local state committed to the repo. Run CI/CD via **GitHub Actions**, authenticating to GCP via **Workload Identity Federation** (no long-lived service account keys stored as GitHub secrets). The pipeline gate, in order: lint/type-check → unit tests (backend `pytest`, frontend Vitest, run in parallel) → build/deploy → post-deploy smoke test against the live Cloud Run URL. CI/CD is designed from the start to run safely against pull requests from forks (not retrofitted later), given the explicit intent to accept external contributions.

**Note — not fully settled:** the Terraform-plus-remote-state approach (vs. the imperative `gcloud` scripting alternative below) is the current working decision, flagged to revisit once actual implementation is under way rather than treated as fully confirmed here.

## Alternatives Considered

### IaC: GCP-native tooling (Deployment Manager / `gcloud infra-manager`)
- Pros: more GCP-native.
- Cons: far smaller ecosystem/community than Terraform, less transferable knowledge if another cloud is ever touched.
- Rejected because: Terraform is the de facto standard and no GCP-native advantage outweighed the ecosystem gap.

### IaC: imperative `gcloud` CLI scripts (no persistent client-side state)
- Pros: simpler mental model, no state artifact to secure at all — the cloud itself is the only source of truth, sidestepping the state-storage question entirely.
- Cons: no automatic dependency ordering or idempotency (must be hand-written); no `plan`-style diff showing what a change will actually do before it happens — a real loss for a project where external contributors may propose infrastructure changes and reviewers benefit from seeing a preview rather than tracing a script by hand.
- **Not rejected outright — deferred**: the current lean is Terraform + remote state, since it closes the specific security-exposure gap this alternative would have avoided (at low cost) while keeping the plan/diff review benefit. Flagged to revisit once real implementation experience with either approach is in hand, not a settled call (see ADR-008).

### CI/CD: Cloud Build
- Pros: tighter native integration with other GCP services (Artifact Registry, Cloud Run), less cross-cloud auth setup than GitHub Actions ↔ GCP.
- Cons: CI config and pipeline logic live in a GCP-specific format, separate from where code review/PRs already happen on GitHub; splits "where I look for CI status" from "where I review code."
- Rejected because: the existing workflow (per project `CLAUDE.md`) is already PR-based on GitHub; Cloud Build would fragment that into two consoles for no offsetting benefit, and Workload Identity Federation removes the main security downside of GitHub↔GCP auth that would otherwise favor Cloud Build.

## Consequences
- CI config lives in the same repo as the code it builds/tests/deploys (monorepo, ADR-002), with path-based triggers to avoid rebuilding/redeploying unrelated services on unrelated changes.
- No static GCP credentials stored in GitHub — Workload Identity Federation issues short-lived tokens per run.
- The post-deploy smoke test (health check + a real form submission against the live deployed service) is the step that actually proves Cloud Run, IAM, Secret Manager, and DNS are wired together correctly — unit tests alone don't prove this.
- A future staging environment (deliberately deferred — see ADR-006) is a config addition to this same pipeline, not a redesign, since environment name is treated as a variable rather than hardcoded.
- Terraform state never enters the repo (public or otherwise) and stays IAM-controlled regardless of the repo's own visibility, while `terraform plan` remains available as a reviewable diff for infrastructure changes proposed by any contributor.
- Workflows that trigger on pull requests must be written with fork-originated PRs in mind from the start — untrusted PR code must never run with access to deploy credentials or secrets.
- This ADR's IaC-tool sub-decision is explicitly open to revisiting during implementation (see Decision note above and ADR-008) — later specs/work should not treat it as unquestionable.
