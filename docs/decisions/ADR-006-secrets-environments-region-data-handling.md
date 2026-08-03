# ADR-006: Secrets management, environments, region & interest-capture data handling

## Status
Accepted

## Date
2026-07-30

## Context
This slice's landing page is capture-only (no LLM/GBIF calls) but still needs runtime secrets (a PostHog API key now; an Anthropic API key later), a decision on whether to run multiple environments, a GCP project/region, and somewhere to put what users type into the interest-capture form. The project has one solo maintainer, no committed user base, and prior test data (Retiro Park) is Madrid-based.

## Decision
- **Secrets**: GCP Secret Manager is the single source of truth for runtime secrets. Cloud Run pulls secret values directly from Secret Manager at startup; GitHub Actions (via Workload Identity Federation, ADR-005) never sees secret values, only enough access to trigger a deploy.
- **Environments**: a single production environment for this slice — no separate dev/staging. Terraform and CI/CD are structured so environment name is a variable, not hardcoded, so adding staging later is a config change.
- **GCP project & region**: a new, dedicated GCP project (clean IAM/billing boundary), region `europe-west1`.
- **Interest-capture data**: submissions (free-text query + timestamp only — no email or other PII collected by design) are written to structured logs (Cloud Logging) only — no database — and also forwarded to PostHog for querying there.

## Alternatives Considered

### Secrets: GitHub Actions secrets only (no Secret Manager)
- Cons: doesn't solve *runtime* secret access for the deployed service itself; secrets would flow through the CI pipeline unnecessarily at deploy time, and rotation means re-deploying rather than updating one place.
- Rejected because: Secret Manager cleanly separates "how CI authenticates" from "what secrets the running app needs," and is the natural foundation for the Anthropic API key protection required by the still-open security/abuse-guardrails slice.

### Environments: dev/staging + prod
- Pros: standard practice, catches a broken deploy before real users see it.
- Cons: meaningfully more IaC, CI/CD complexity, and secrets-per-environment to keep in sync, for a page whose actual blast radius if broken is a static "coming soon" page and one form.
- Rejected because: disproportionate to current risk; deferred until Phase 2 ships something with real user-facing risk if it breaks.

### Data storage: Firestore
- Pros: a real, queryable, exportable store; would preview a pattern reusable for Phase 2 walk-caching.
- Cons: more infrastructure than the volume/purpose justifies for what the user explicitly described as "a purely test case" without much expected real, useful data, especially once PostHog covers querying.
- Rejected because: explicit user direction — structured logs plus PostHog cover the actual need at this stage.

### Data storage: Cloud SQL
- Cons: an always-on relational instance with real cost even at zero traffic, no relational structure to justify it for a single text field + timestamp.
- Rejected because: clear overkill for this stage.

### Region/PostHog hosting: US-based (default)
- Cons: given the GDPR-consent design already required for PostHog (ADR-007), US-hosted infrastructure would sit awkwardly next to an EU-consent story with no data-residency consideration.
- Rejected because: EU-leaning defaults (`europe-west1` + PostHog EU Cloud) keep the infrastructure story consistent with the consent story, and cost nothing extra to choose deliberately now.

## Consequences
- No secret values ever touch the Docker image, the repo, or GitHub Actions logs.
- Adding a staging environment later requires new Terraform workspace/variable values, not a redesign of the pipeline.
- Interest-capture submissions are not treated as a system of record — reviewing them means querying Cloud Logging or PostHog, not a database; if this data later needs richer querying/export, a real store (Firestore was the leading candidate) would need to be introduced.
- Infra and third-party analytics hosting both default to the EU; if the real user base later skews elsewhere, this is a reversible choice but not a zero-cost one (region migration, PostHog project region change).
