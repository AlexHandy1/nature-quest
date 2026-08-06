# ADR-010: Real-time per-query email alert, in place of (not instead of) REQ-025/026/027's uptime/error-rate alerting

## Status
Accepted — a deliberate, explicitly-scoped deviation from `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`'s REQ-025/026/027 and `GUD-003`. Those requirements (uptime check, uptime-failure alert, 5xx-rate alert) are **still not built** — this ADR covers a different, additional alert that was built instead, first.

## Date
2026-08-06

## Context
After Slice 2 deployed, the immediate want was direct visibility into product usage — knowing when someone actually submits a query — rather than site-health monitoring (is the service up, is it erroring). `GUD-003` explicitly scoped this slice's alerting to "uptime + error-rate only... no Slack/PagerDuty integration," deliberately narrow. A real-time, per-request alert is a different shape of thing than either of those: it's a traffic/product signal, not a health signal, and it fires on **every** matching event rather than a threshold breach.

This was raised and flagged explicitly before building: a per-query email alert will not scale — even modest real traffic would turn into inbox spam within days, which is exactly the failure mode `docs/FEATURE_IDEAS_BACKLOG.md`'s "Daily email activity digest" idea was already scoped to avoid (a rollup, deliberately not per-event). Built anyway, as an accepted short-term trade-off: current traffic is near-zero (pre-launch), and the maintainer wanted real-time per-query visibility specifically while validating the app works, not a long-term monitoring posture.

## Decision
- A GCP log-based metric (`google_logging_metric.query_outcome`, `infra/monitoring.tf`) counts every `query_outcome` structured log line REQ-017 already emits (one per `/api/query` request, every outcome).
- An alert policy (`google_monitoring_alert_policy.query_submitted`) fires whenever that metric's count is `> 0` in a 60-second window, `duration = "0s"` — i.e. near-immediately on any single matching log entry, not a sustained-breach threshold like a normal alert policy.
- Reuses the same email notification channel pattern REQ-026/027 already call for (`CON-003`: undefaulted Terraform variable, `alert_email`, never committed).

## Alternatives Considered

### Build REQ-025/026/027 first (uptime check + uptime-failure + 5xx-rate alerts), as originally spec'd
- Pros: matches the committed spec exactly, addresses the actually-scoped-for risk (site down / erroring, not visible to the maintainer).
- Cons: doesn't answer the question actually being asked in the moment ("did anyone just use the app").
- Rejected for now, not permanently — still the next alerting work item.

### Daily email digest instead of real-time per-query alert
- Pros: scales fine, was already the recorded plan for this exact need (`docs/FEATURE_IDEAS_BACKLOG.md`).
- Cons: doesn't give real-time visibility, which was explicitly what was wanted for this pre-launch validation window.
- Rejected for *now* — this remains the intended eventual replacement once real traffic makes per-query alerting untenable.

## Consequences
- **This will not scale and is known not to.** No cap, no batching, no digest — one email per query, indefinitely, until manually changed. Revisit before any real launch traffic, or the moment this becomes noisy rather than useful.
- REQ-025 (uptime check) and REQ-027 (5xx-rate alert) remain **not built** — this ADR does not satisfy them. `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`'s REQ-025/026/027/AC-016/AC-017 are still open work, tracked separately.
- **Known GCP gotcha hit while building this**: creating a `google_monitoring_notification_channel` via Terraform (the direct API) does **not** automatically send the verification email GCP's Console UI flow normally triggers — had to call `notificationChannels.sendVerificationCode` explicitly, then `notificationChannels.verify` with the code, via direct REST calls (`gcloud alpha monitoring channels` isn't installed by default and wasn't used). Also hit a real propagation delay: a freshly-created `google_logging_metric` isn't immediately queryable by `google_monitoring_alert_policy`'s API (~404 "Cannot find metric" for a couple of minutes) — Terraform apply needed a retry after a short wait, not a code fix.
- **Known GCP Console gotcha**: the console's project selector (top nav) can silently retain a previously-viewed project even when a page URL includes `?project=X` — led to a false "nothing is configured" read of the Console UI when the underlying API calls (correctly scoped to `nature-quest-504414`) showed the resources genuinely existed. Always confirm the top-nav project selector explicitly before trusting what a GCP Console page shows.
