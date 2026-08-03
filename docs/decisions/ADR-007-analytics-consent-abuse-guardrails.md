# ADR-007: Analytics, consent, and abuse protection posture

## Status
Accepted

## Date
2026-07-30

## Context
The app collects product analytics (PostHog) and exposes a public-facing submission endpoint. This requires a consent-compliant approach to tracking, and a baseline defensive posture for the public endpoint. The repo — including, eventually, the code implementing these protections — is public from day one (ADR-008), so no control here may depend on an attacker not being able to read how it works. Implementation-level detail on the specific protective mechanisms is intentionally omitted from this document for that reason.

## Decision
- **Analytics**: PostHog (EU-hosted instance), capturing both client-side and server-side events for the same key actions, so capture is verifiable even if one channel is unavailable.
- **Consent**: analytics initialize in a no-capture-by-default state; tracking only begins after explicit user opt-in, enforced by the analytics SDK itself rather than custom application logic.
- **Abuse protection**: baseline automated-traffic mitigation and request-rate limiting are applied to public-facing submission endpoints, proportionate to the endpoint's actual risk at each stage of the product's build-out.

## Alternatives Considered

### A packaged third-party consent-management platform
- Rejected as disproportionate tooling for the current scope; the analytics SDK's own consent controls are sufficient for now, and this can be revisited if legal/traffic scope grows.

### No abuse protection at this stage
- Rejected — even a low-risk, low-traffic endpoint benefits from baseline protection, and it's cheap to add early rather than retrofit later.

## Consequences
- No tracking identifier is set for a visitor until they actively consent.
- The legal sufficiency of the consent flow's exact wording/scope is not resolved by this ADR and should be reviewed before any high-traffic launch.
- Abuse protection appropriate to a purely public-facing, LLM-free endpoint has been applied. Protections specific to LLM-backed surfaces (cost/misuse guardrails) are a distinct, separately-scoped piece of work tied to when those surfaces go live, not covered here.
- Any mechanism chosen must remain effective if fully read from the public source code — see ADR-008.

**Implementation note (2026-08-03):** server-side capture was deferred during initial build of the production-foundation spec (`docs/specs/spec-infrastructure-production-foundation-300726.md`, REQ-013/016) — client-side capture only for now. See that spec for the reasoning and the `distinct_id`-passthrough design to use when server-side capture is added.

**Implementation note (2026-08-03):** the Cloud Armor + load-balancer setup (REQ-017) was deferred during Terraform build — it requires a full external HTTPS Load Balancer (real, ~$25/month fixed infrastructure) disproportionate to current "coming soon" traffic. Cloud Run's `max_instance_count` (set to 2) is used instead as an interim, free guardrail against cost-creep/runaway scaling. This does not address the data-pollution/spam risk Cloud Armor's per-IP rate limiting would — that risk is knowingly accepted for now given the low-traffic stage. Revisit before any real public launch.
