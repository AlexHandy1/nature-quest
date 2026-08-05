---
title: LLM Infra & Abuse Guardrails, with a First GBIF Taxon-Query Wrapper
version: 1.0
date_created: 2026-08-04
last_updated: 2026-08-04
tags: [tool, infrastructure]
status: Design complete, not yet built
sources:
  - production: app/backend/main.py, routers/interest.py, models/interest.py, services/logging_client.py, Dockerfile, infra/cloud_run.tf, infra/secret_manager.tf, app/frontend/src/App.tsx, InterestForm.tsx, ConsentBanner.tsx, lib/posthog.ts
  - prototype: prototypes/scripts/intent_query_spike.py, prototypes/scripts/e2e_walk_spike_clustering.py, prototypes/scripts/e2e_walk_spike_full_validation.py, prototypes/reference/gbif_docs_summary.md, gbif_kingdom_keys.json, gbif_common_class_keys.json, gbif_common_order_keys.json
  - docs/status_docs/PLANNING_INTENT_QUERY_210726.md, PLANNING_LLM_GUARDRAILS_040826.md, WORK_SUMMARY_030826.md, WORK_SUMMARY_250726.md
  - docs/prds/nature-quest-prd-300726.md
  - docs/decisions/ADR-005, ADR-006, ADR-007, ADR-008
  - docs/specs/spec-infrastructure-production-foundation-300726.md
---

# Introduction

This spec covers Slice 2 of the Nature Quest PRD: **security & abuse guardrails for the app's first LLM integration**, combined with a deliberately thin, first version of the GBIF query wrapper — so the guardrails can be proven against a real free-text input, a real LLM call, and a real third-party API integration, not designed in the abstract. It replaces the interest-capture form built in Slice 1 with the first real feature: a free-text box that maps a request to a single high-level taxon category and returns a real species list from GBIF.

This document is meant to be implementable by an agent with no other context from this project. Read it fully before writing code.

# 1. Purpose & Scope

**In scope:**
- One backend endpoint that takes free text, makes one structured-output LLM call to resolve it to a single GBIF taxon filter (or no match), and — if resolved — queries GBIF for a real species list over a fixed park and year range.
- Two independent cost/abuse guardrails (per-caller rate limiting, and a global daily ceiling on LLM calls), input validation, and safe handling of the Anthropic API key.
- Resilience around the GBIF call itself (retry, timeout, a known scale problem with common taxa over a multi-year range).
- Structured operational logging plus two PostHog-based observability channels (client-side product funnel, server-side LLM trace/cost capture), both wired to a shared visitor identity for the first time in this project.
- Replacing the interest-capture form on the landing page with this new query box.
- Basic uptime/error-rate alerting to the maintainer's email — the first alerting this project has, sitting on top of the logging/observability work above rather than replacing it.

**Explicitly out of scope for this slice (deferred to later PRD slices):**
- Mixed-taxa queries (e.g. "birds, plants and mammals" resolving to multiple parallel filters) — Slice 3+.
- The `q` free-text passthrough param to GBIF's `occurrence/search` — dropped for this slice; the LLM's structured output here is `taxonFilter` only, not the full schema from `PLANNING_INTENT_QUERY_210726.md` §3.
- `sort`/rarity ("show me something rare") — always `most_observed` for this slice.
- Waypoint ordering, route generation, map rendering, narrative generation, draw-your-own-area — all later PRD slices (3-13); this slice stops at a species list.
- Removing the now-unused `POST /api/interest` route, `InterestSubmission` model, and `log_interest_submission` — kept in place, dormant, as the first and only previously-working end-to-end route, until a later cleanup slice removes it.
- A general LLM/AI provider abstraction (Slice 11) — this slice is Anthropic-only, same as all prior prototyping.

# 2. Verified Facts

**GBIF API mechanics** (verified live against `api.gbif.org/v1/`, `PLANNING_INTENT_QUERY_210726.md` §2, curated into `prototypes/reference/gbif_docs_summary.md`):
- `GET /occurrence/search` has **no string taxonomy filter** — `kingdom=Plantae`-style params are silently ignored, not an error. Taxonomy filtering is numeric-key-only (`kingdomKey`, `classKey`, `orderKey`, etc.).
- `GET /v1/species/match?name={name}&rank={RANK}` resolves a lay name + rank to a numeric key. `matchType` is `EXACT`, `FUZZY`, or `NONE`. On no match, most response fields are absent.
- Validation rule already decided and verified: accept `EXACT`, or `FUZZY` with `confidence >= 85`; anything else is unresolved.
- Unranked clade names (e.g. `Vertebrata`, `Tetrapoda`) must never be used as `taxonValue` — they either fail to resolve or resolve to unrelated organisms in the backbone taxonomy (`PLANNING_INTENT_QUERY_210726.md` §2.4). Scoping `taxonRank` to `kingdom | phylum | class | order | family | genus` (already the schema's enum) already prevents this.

**Curated resolution caches** — already built, verified, and reusable as-is from `prototypes/reference/`:
- `gbif_kingdom_keys.json` — all 9 GBIF backbone kingdom values.
- `gbif_common_class_keys.json` — 10 common Animalia classes (birds, insects, mammals, amphibians, arachnids, gastropods, plus the 4-class reptile union: Crocodylia/Squamata/Testudines/Sphenodontia).
- `gbif_common_order_keys.json` — 5 fish orders, a data-driven ~67%-coverage holding solution (fish has no single backbone class).
- Resolution order: check the relevant curated cache first by rank, fall through to a live `species/match` call only on a cache miss.

**Fixed location and year range** (`prototypes/scripts/waypoint_spike.py`, `e2e_walk_spike_full_validation.py`, confirmed in `WORK_SUMMARY_250726.md`):
- `GBIF_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"` (Retiro Park, Madrid).
- `YEAR_RANGE = "2023,2026"` — verified inclusive of both full years (not a rolling window) by summing per-year counts against the combined range.

**GBIF scale problem with common taxa over this range** (`WORK_SUMMARY_250726.md`): an unfiltered/common-taxon query (e.g. birds) over `2023,2026` in the Retiro polygon returned **~57,000 occurrences**, requiring ~190 sequential 300-per-page requests to fully paginate (species-ranking here requires enumerating raw occurrences — GBIF's `occurrence/search` has no server-side species-count facet in use). The mitigation already built and verified in `e2e_walk_spike_clustering.py`'s `fetch_gbif_occurrences()`: probe the total count first (`limit=0`, cheap), and if it exceeds a threshold, fall back from the full year range to the single latest year before paginating for real. This pattern is reused in this slice (see §6) — "birds" resolving to `class/Aves` is exactly the kind of category likely to trigger it.

**GBIF transient-failure handling** (`WORK_SUMMARY_250726.md`): a transient non-dict/non-200 GBIF response was observed crashing a parallel fetch under load; mitigated with a small retry-with-backoff wrapper around the raw `gbif_search()` call. Not reproduced on retry — treated as GBIF-side flakiness, not a code bug.

**Backend runtime shape** (production: `app/backend/Dockerfile`): `uvicorn` runs with no `--workers` flag — **one worker process per Cloud Run instance**. This makes an in-process, thread-lock-guarded module-level counter valid for intra-process concurrency (multiple requests within one instance), with the known caveat that state does not survive a process restart or exist across multiple instances (Cloud Run `max_instance_count = 2`, `infra/cloud_run.tf`).

**Secret Manager scaffold already exists, empty** (`infra/secret_manager.tf`): `backend_secret_names` is currently `[]`, provisioning a Secret Manager secret + `roles/secretmanager.secretAccessor` IAM binding on the Cloud Run runtime service account (`google_service_account.run`) for each name added — built in Slice 1 specifically anticipating this slice's Anthropic key.

**`distinct_id` passthrough was explicitly deferred in Slice 1**, not solved (`spec-infrastructure-production-foundation-300726.md` REQ-013/016, `ADR-007` implementation note): "server-side capture requires a `distinct_id`... judged excessive for getting the basics running... When server-side capture is built, the payload should carry the client's PostHog `distinct_id`... rather than reinventing one server-side." This slice is where that gets built, for the first time.

**Security-detail disclosure precedent already established in this project** (`ADR-007`, `ADR-008`, and Slice 1's own spec `REQ-017`/`GUD-002`): the codebase is public from its first commit, so "no security control may depend on obscurity" — but the project's own convention, already applied in the Slice 1 spec, is to **name the category of protective mechanism in written specs without publishing its exact tuned parameters** (e.g. Slice 1 named Cloud Armor rate limiting as the mechanism without stating its threshold). This spec follows the same convention for the guardrails below — exact numeric thresholds were agreed during this slice's design conversation but are deliberately not restated here; see `CON-002` and `GUD-001`.

# 3. Definitions

- **GBIF**: Global Biodiversity Information Facility, the public species-occurrence API this feature queries.
- **`taxonRank` / `taxonValue`**: a GBIF backbone rank (`kingdom`, `class`, etc.) and the lay-term scientific name at that rank, as produced by the LLM before code-side resolution to a numeric key.
- **`distinct_id`**: PostHog's per-visitor identifier, generated client-side; passing it to the backend lets server-side events attribute to the same visitor as client-side events.
- **AI Observability**: PostHog's LLM-specific product — auto-captures Anthropic SDK calls (input, output, tokens, cost, latency) as `$ai_generation` events via OpenTelemetry instrumentation.
- **Eval suite**: a distinct test tier that calls the real Anthropic API (and, for one tier, real GBIF) to check actual model/pipeline behavior against curated expectations — not part of the standard, deterministic, CI-gating test suite.
- **Scale guard**: the probe-then-fallback mechanism that avoids full-range pagination cost for common taxa (see §2, §6).

# 4. Requirements, Constraints & Guidelines

**Endpoint & request handling**
- **REQ-001**: A new `POST /api/query` endpoint accepts a free-text query and a client-supplied PostHog `distinct_id`, and returns one of four outcomes (`resolved`, `unresolved`, `no_results`, `gbif_unavailable`) — see §5.
- **REQ-002**: Input is validated server-side: rejected if empty/whitespace-only, and rejected above a fixed maximum length. **`CON-002` applies** — the exact length ceiling is a build-time constant, not specified in this document.
- **CON-001**: This endpoint replaces `POST /api/interest` as the landing page's sole interactive feature (see §11 frontend). `POST /api/interest` itself, `InterestSubmission`, and `log_interest_submission` are left in place, unused by the frontend, pending a future cleanup slice — do not delete them as part of this slice.

**Cost & abuse guardrails**
- **REQ-003**: Per-caller request-rate limiting is applied to `POST /api/query`, using `slowapi` (FastAPI/Starlette port of Flask-Limiter, wraps the `limits` library) with an in-memory storage backend, keyed by caller IP.
- **REQ-004**: A separate, global daily ceiling on the number of LLM calls made by the app is enforced independently of REQ-003 — implemented as a small module-level counter (date + count, `threading.Lock`-guarded, per §2's single-worker-process fact) that resets automatically on a date change, checked before every Anthropic API call.
- **CON-002 (security-detail disclosure)**: Per the project's established convention (`ADR-007`/`ADR-008`, §2 above), the exact numeric thresholds for REQ-002 (max input length), REQ-003 (requests/minute), and REQ-004 (LLM calls/day) are **not recorded in this document**. They are build-time constants (or environment-variable-configurable, implementer's choice) chosen at implementation time, informed by the project's low-traffic, solo/pre-launch stage and the cost profile of a single small structured-output call. Neither mechanism's effectiveness depends on the number being secret — both remain valid protections even though the numbers will be visible in the public source code once implemented; this is a disclosure-hygiene convention, not a security assumption.
- **GUD-001**: Both REQ-003 and REQ-004 use in-memory state, not a shared/persistent store (Firestore, Redis, GCS). This is a deliberate, explicit trade-off: both counters reset on redeploy, cold start, or when Cloud Run runs more than one instance concurrently (`max_instance_count = 2` per Slice 1). Accepted because: (a) the true worst-case backstop is the account-level Anthropic billing hard-stop that already exists outside this app, (b) at this project's current solo/pre-launch traffic, the realistic reset frequency is bounded by the maintainer's own deploy cadence, not adversarial behavior, and (c) it avoids new infrastructure (a database, a cache service) disproportionate to current risk — consistent with `ADR-007`'s "proportionate to actual risk" posture. Do not "fix" this by introducing Firestore/Redis/GCS-backed persistence or by pinning `min_instance_count`/`max_instance_count` to 1 without an explicit follow-up decision — both were considered and rejected during this slice's design (see §9).
- **REQ-005**: The Anthropic API key is delivered via the empty `backend_secret_names` scaffold in `infra/secret_manager.tf` (add `"anthropic-api-key"`) — but the application fetches it **explicitly at startup via the Secret Manager client library**, not via Cloud Run's native `secret_key_ref` env-var injection. See §9 for why this differs from the simpler env-var-injection approach used elsewhere in this project.
- **GUD-002**: Never log, or pass into `extra=` on the structured logger (`JsonLogFormatter`, `main.py`), anything resembling a wholesale dump of `os.environ` or `vars()` — this is the specific accidental-leak pattern REQ-005's design choice is protecting against.

**LLM call & taxon resolution**
- **REQ-006**: One non-agentic Anthropic Messages API call per query, using tool-use/structured output (same pattern as `intent_query_spike.py`'s `produce_gbif_query` tool, schema in §5), model `claude-haiku-4-5-20251001`. Corrected during implementation: this document previously specified `claude-sonnet-5` per `PLANNING_INTENT_QUERY_210726.md` §7, but later prototype work (`prototypes/README.md`) found Haiku matches Sonnet's output quality on this call shape for roughly half the cost and faster wall time, and switched the pipeline's default accordingly (`e2e_walk_spike.py`, `server.py`) — that later finding supersedes the earlier decision.
- **REQ-007**: The LLM's output schema for this slice is reduced from the original design (`PLANNING_INTENT_QUERY_210726.md` §3) to a single optional `taxonFilter: {taxonRank, taxonValue} | null` — no `taxonFilters` list (no mixed-taxa), no `q`, no `sort` (`most_observed` is the only mode, applied unconditionally in code, not requested from the LLM).
- **REQ-008**: Resolution of `{taxonRank, taxonValue}` to a numeric GBIF key follows the existing, verified pattern: check the matching curated cache first (kingdom/class/order per §2), fall through to a live `species/match` call on a miss, apply the `EXACT` / `FUZZY≥85` validation rule. On a `null` `taxonFilter` from the LLM, or a filter that fails resolution, the request is `unresolved` (REQ-010) — **no GBIF call is made** in this case (explicit product decision — this is the branch where the pipeline proves it correctly does *not* call the third-party API, not a fallback-to-default-search branch).

**GBIF query & resilience**
- **REQ-009**: A resolved filter is queried against `GET /occurrence/search` with the fixed `GBIF_POLYGON`, `hasCoordinate=true`, `occurrenceStatus=PRESENT`, and the resolved `*Key` param, applying the scale-guard pattern from §2 (`e2e_walk_spike_clustering.py`'s `fetch_gbif_occurrences()`): probe count with `limit=0`; if over the guard threshold, use the single latest year instead of the full `2023,2026` range before paginating for real. **`CON-002` applies to the threshold value.**
- **REQ-010**: The GBIF call is wrapped with retry-with-backoff and a hard timeout, reusing the pattern already proven in `e2e_walk_spike_clustering.py`'s `gbif_search()` wrapper. **`CON-002` applies to the retry count/backoff/timeout values.** If GBIF still fails after retries, the response is `gbif_unavailable` (REQ-013).
- **REQ-011**: Species ranking/selection reuses the existing verified logic (`rank_species()` in `intent_query_spike.py`): group raw occurrences by species, rank by observation count descending (`most_observed` only), and return the top 5 with `species` (scientific name), `count`, `kingdom`, and a simple average `hotspot_lat`/`hotspot_lon` (not density-clustered via the adaptive N×N grid built in `e2e_walk_spike_clustering.py` — that's PRD Slice 4's job, out of scope here since this slice has no waypoint/route to place it for).
- **REQ-012**: If a resolved taxon genuinely returns zero GBIF occurrences, the outcome is `no_results` (§5) — distinct from `unresolved`. Expected to be rare given Retiro's data density, the high taxon levels used, and the multi-year range (§2), but must be a real, tested code path, not assumed unreachable.

**Response contract**
- **REQ-013**: Four distinct outcomes, all under `POST /api/query`: `resolved` (200, LLM matched + GBIF returned species), `unresolved` (200, LLM found no match, no GBIF call made), `no_results` (200, LLM matched + GBIF call made + zero species returned), `gbif_unavailable` (502, LLM matched but GBIF failed after retries). See §5 for exact shapes.
- **REQ-014**: `429` responses (from REQ-003 and REQ-004) are distinguishable from each other — different `error` field values and different, purpose-appropriate message copy — since they imply different retry timeframes (seconds/minutes vs. the next day) and the frontend must render them differently (REQ-019).
- **REQ-015**: The `gbif_unavailable` outcome is logged/observed as a distinct signal (REQ-016) but shown to the end user as brief, non-technical copy (e.g. an issue reaching nature data, try again shortly) — not a stack-trace-flavored or GBIF-specific error message.
- **REQ-016**: A GBIF failure (REQ-015) still counts against the daily LLM-call budget (REQ-004) — the LLM cost was already incurred regardless of what happened downstream.

**Observability**
- **REQ-017**: Every `POST /api/query` request produces one structured log line (via the existing `JsonLogFormatter`/Cloud Logging pattern, `main.py`) — always-on, not consent-gated, since it is operational data, not analytics — including: the query text, the resolved outcome (`resolved`/`unresolved`/`no_results`/`gbif_unavailable`/`rate_limited`/`daily_limit_reached`), which guardrail (if any) fired, token usage when an LLM call was made, and the GBIF result count when a GBIF call was made.
- **REQ-018**: A client-side PostHog event fires for the query funnel (submitted → outcome), following the existing consent-gated pattern (`lib/posthog.ts`, `ConsentBanner.tsx`) — no event before consent, same as Slice 1.
- **REQ-019**: PostHog's **AI Observability** product is wired in server-side (OpenTelemetry instrumentation of the Anthropic SDK — `AnthropicInstrumentor().instrument()` + `PostHogSpanProcessor`), auto-capturing every real Anthropic call as an `$ai_generation` event (input, output, tokens, cost, latency). **This is gated by the same client-side consent decision as REQ-018**, not fired unconditionally — implemented by having the frontend send its current PostHog `distinct_id` and consent state with each query, and the backend only instrumenting/emitting the AI Observability span when consent was granted.
- **REQ-020**: The frontend's `POST /api/query` request body carries the client's PostHog `distinct_id` (already an anonymous identifier, no new PII, consistent with the passthrough design already anticipated in `ADR-007`'s Slice 1 implementation note) so REQ-019's server-side events and the client-side product-analytics events attribute to the same visitor and are joinable in PostHog. **`[NEEDS INPUT during implementation]`: confirm via PostHog's own docs/dashboard behavior whether events sharing a `distinct_id` join automatically in PostHog's UI, or whether additional configuration is needed — flagged during design as an assumption to verify, not confirmed against live PostHog behavior.**
- **REQ-021**: Requests rejected by REQ-003/REQ-004 (rate-limited, daily-cap) never reach the LLM call, so they are **not** captured by REQ-019 (AI Observability only sees real Anthropic calls) — they are visible only via REQ-017's structured log. This is intentional, not a gap to close.
- **REQ-022**: **PostHog AI Evals** is configured against the `$ai_generation` events already flowing in via REQ-019 — at minimum, one code-based (Hog) evaluation checking that a captured generation's output is well-formed (a valid `taxonFilter` shape or an explicit null), which costs nothing to run and needs no LLM judge. This operates entirely within PostHog on real/eval traffic already being captured — it is a **live, ongoing quality-monitoring layer, not a pre-merge test gate**, and is explicitly complementary to, not a replacement for, REQ-024's pytest-run eval suite (see §9 for why both are kept).

**Testing**
- **REQ-023**: Standard project TDD (`/tdd`, `/testing`) applies to all deterministic code in this slice — input validation, taxon resolution/cache lookups, the rate limiter, the daily-budget counter, the scale-guard/retry/timeout wrapper, and response-shaping for each of the four outcomes. The prototype's "light TDD, LLM/network calls untested" convention does **not** carry over to this production code (per `/create-technical-spec`'s own instruction) — this project's usual TDD approach applies, adapted to this slice's specific choice (REQ-024) of also building a real eval layer for the LLM/GBIF behavior itself.
- **REQ-024**: A second, distinct test tier — a **pytest-marked eval suite** (e.g. `@pytest.mark.eval`), excluded from the default `pytest` run and from CI's `unit-tests-backend` gate, runnable on demand (`pytest -m eval`), **built on DeepEval** (open-source, pytest-native LLM eval framework — "Pytest-style assertions… built to run with pytest and CI providers") rather than bespoke `assert` statements — is built with two levels, both starting with exactly one populated case ("I want to see birds"):
  - **Tier 1 (LLM structured-output eval)**: a real Anthropic call, asserting the resolved `taxonFilter` matches expectation (`{"taxonRank": "class", "taxonValue": "Aves"}`) via a DeepEval test case/exact-match-style metric.
  - **Tier 2 (end-to-end eval)**: the full pipeline including a real GBIF call, asserting loose/structural expectations (non-empty species list, every returned species genuinely belongs to the expected taxon) rather than exact-match snapshots — chosen specifically to avoid brittleness against GBIF's occurrence data changing over time.
  - This is explicit foundation-building, not a comprehensive suite — designed to be extended with more cases (and, as query sophistication grows, DeepEval's LLM-as-judge metrics for less binary correctness checks) in later slices (see PRD Slice 12, "Evaluation harness").

**Basic infrastructure alerting**
- **REQ-025**: A GCP Cloud Monitoring **uptime check** is configured against the deployed Cloud Run service's public `GET /health` endpoint, polling on a regular interval — the first automated "is the site actually up" signal this project has (until now, only the CI/CD post-deploy smoke test checked this, once, at deploy time).
- **REQ-026**: A **Cloud Monitoring alerting policy** fires when REQ-025's uptime check fails for a small number of consecutive checks, notifying a GCP **email notification channel** pointed at the maintainer's own email address.
- **REQ-027**: A second, separate alerting policy watches Cloud Run's own request-count metric filtered to 5xx responses, firing on an elevated error rate over a short window, notifying the same email channel. This is explicitly the first, narrowest instance of "broader error-pattern alerting" — a foundation to extend later (e.g. alerting on sustained `daily_limit_reached`/`rate_limited` rates, or an LLM-cost anomaly), not a comprehensive alerting system built in this slice.
- **CON-003**: The email address behind REQ-026/027's notification channel is supplied via a Terraform variable with **no committed default** (set via a local `.tfvars` file kept out of version control, or a CI-provided variable) — not hardcoded into `monitoring.tf`. The repo is public from day one (`ADR-008`); a personal email address is exactly the kind of detail that shouldn't be committed just because it isn't formally a secret.
- **GUD-003**: Keep this first pass at alerting deliberately narrow — uptime + error-rate only, one email channel, no paging/escalation policy, no Slack/PagerDuty integration. This is a starting foundation per its own stated purpose ("so I can see something going wrong and act"), appropriate to a solo, pre-launch project; broader/more granular alerting is future work, not this slice's scope.

# 5. Interfaces & Data Contracts

## `POST /api/query`

**Request:**
```json
{
  "query": "<free text>",
  "distinctId": "<client PostHog distinct_id>"
}
```

**Response `200`, resolved:**
```json
{
  "status": "resolved",
  "taxonRank": "class",
  "taxonValue": "Aves",
  "species": [
    {"species": "Turdus merula", "count": 42, "kingdom": "Animalia", "hotspot_lat": 40.4132, "hotspot_lon": -3.6828}
  ],
  "message": "<short copy noting this is an early preview of a much bigger walk experience to come>"
}
```

**Response `200`, unresolved:**
```json
{
  "status": "unresolved",
  "message": "Sorry, we couldn't match that to a category we support yet — try something like 'birds' or 'plants'."
}
```

**Response `200`, no_results:**
```json
{
  "status": "no_results",
  "taxonRank": "class",
  "taxonValue": "Aves",
  "message": "<explains a valid category was understood, but nothing was found for it right now>"
}
```

**Response `502`, gbif_unavailable:**
```json
{
  "status": "gbif_unavailable",
  "message": "<generic, non-technical copy — we're having trouble reaching nature data right now, try again shortly>"
}
```

**Response `429`, rate-limited (REQ-003):**
```json
{ "error": "rate_limited", "message": "<short, fast-retry copy>" }
```

**Response `429`, daily cap (REQ-004):**
```json
{ "error": "daily_limit_reached", "message": "<friendly copy, e.g. we've reached today's limit — try again tomorrow>" }
```

**Response `422`:** FastAPI's standard validation-error shape when `query` is missing, empty, or exceeds the max length (`CON-002`).

## LLM structured-output tool schema (adapted from `intent_query_spike.py`'s `QUERY_SCHEMA_TOOL`)

```json
{
  "name": "produce_gbif_query",
  "description": "Translate the user's natural-language nature-walk request into a single GBIF taxon filter, or none if there's no clear taxonomic signal.",
  "input_schema": {
    "type": "object",
    "properties": {
      "taxonFilter": {
        "type": ["object", "null"],
        "description": "A single scientific rank + name pair, never a numeric key. Null if the request has no clear taxonomic signal.",
        "properties": {
          "taxonRank": {"type": "string", "enum": ["kingdom", "phylum", "class", "order", "family", "genus"]},
          "taxonValue": {"type": "string"}
        },
        "required": ["taxonRank", "taxonValue"]
      }
    },
    "required": ["taxonFilter"]
  }
}
```

Note this drops `q` and `sort` entirely from the original design's schema (`PLANNING_INTENT_QUERY_210726.md` §3) and changes `taxonFilters` (array) to `taxonFilter` (single, nullable object), per REQ-007.

## PostHog events

| Event | Trigger | Fired from | Consent-gated |
|---|---|---|---|
| `query_submitted` | User submits the query box | Client-side | Yes |
| `query_outcome` | Response received (with `status`/`error` value) | Client-side | Yes |
| `$ai_generation` | Real Anthropic call made | Server-side (OTel auto-capture) | Yes (REQ-019) |

# 6. Implementation Mechanics

**New backend files** (following the existing `routers/`/`models`/`services/` structure, `app/backend/main.py`):
- `routers/query.py` — the `POST /api/query` route, `slowapi` limiter decoration (REQ-003).
- `models/query.py` — Pydantic request/response models for the four outcomes.
- `services/anthropic_client.py` — wraps the Messages API call (REQ-006), fetches the API key from Secret Manager at app startup (REQ-005) rather than reading it from env.
- `services/taxon_resolution.py` — cache lookup + `species/match` fallback (REQ-008), reusing/promoting the curated JSON reference files from `prototypes/reference/` into the backend package (e.g. `app/backend/reference/`).
- `services/gbif_client.py` — the scale-guard/retry/timeout-wrapped occurrence search + species ranking (REQ-009, REQ-010, REQ-011).
- `services/query_budget.py` — the module-level daily counter (REQ-004, GUD-001).
- `services/ai_observability.py` — OTel/`PostHogSpanProcessor` setup for REQ-019, invoked conditionally per-request based on the consent flag in the request.
- Extend `main.py`'s `create_app()` to include the new router (above the static mount, per the existing comment there) and to initialize the Secret-Manager-fetched Anthropic client and `slowapi`'s limiter/exception handler at app startup.

**New dependencies** (`app/backend/requirements.txt`): `anthropic`, `slowapi`, `google-cloud-secret-manager`, `posthog[otel]`, `opentelemetry-sdk`, `opentelemetry-instrumentation-anthropic`. **New dev/test dependency** (`app/backend/requirements-dev.txt`): `deepeval`, for REQ-024's eval suite. REQ-022 (PostHog AI Evals) needs no new package — it's configured within the PostHog project itself (a Hog-code rule against captured `$ai_generation` events), not application code.

**Infra changes** (`infra/`):
- `secret_manager.tf`: add `"anthropic-api-key"` to `backend_secret_names`.
- New `monitoring.tf`: `google_monitoring_notification_channel` (type `email`, address from an undefaulted Terraform variable per `CON-003`), `google_monitoring_uptime_check_config` targeting the Cloud Run service's `/health` path, and two `google_monitoring_alert_policy` resources (REQ-026 uptime-failure, REQ-027 5xx-rate) referencing that channel.
- No other Terraform changes required — reuses the existing IAM-binding-per-secret pattern, no new bucket/database/instance-count changes (per `GUD-001`'s explicit rejection of Firestore/GCS/instance-pinning alternatives — see §9).

**Frontend changes** (`app/frontend/src/`):
- Replace `InterestForm.tsx` on `App.tsx`'s landing page with a new `QueryForm.tsx` (or similar) that POSTs to `/api/query`, includes the current PostHog `distinct_id` (`posthog.get_distinct_id()`) and consent state in the request, and renders the four response outcomes distinctly, plus the two `429` cases with different copy/timeframe framing (REQ-014).
- Update `ConsentBanner.tsx`'s copy to explain query collection specifically (what's collected and why — to learn what people are searching for and improve the experience), while consent continues to gate only the PostHog/observability layer, not use of the query feature itself (per design decision — REQ-018/019 are consent-gated, the feature itself is not).
- `lib/posthog.ts`: no structural change needed — `distinct_id` is already available via `posthog.get_distinct_id()` once initialized.

**Testing layout** (`app/backend/tests/`): standard test files for the new deterministic units (REQ-023), plus a distinct eval module/marker configuration for REQ-024 (e.g. `tests/evals/`, using DeepEval's `assert_test`/test-case API under a `pytest.ini`/`pyproject.toml` `eval` marker, excluded via `-m "not eval"` in CI's existing `unit-tests-backend` job). REQ-022 (PostHog AI Evals) has no backend code footprint — it's set up in the PostHog project UI once REQ-019's instrumentation is live, not part of this file layout.

# 7. Acceptance Criteria

- **AC-001**: Given a query resolving to a curated-cache taxon (e.g. "I want to see birds"), when submitted to `POST /api/query`, then the response is `resolved` with `taxonRank: "class"`, `taxonValue: "Aves"`, and a non-empty `species` list, and the curated cache was used (not a live `species/match` call).
- **AC-002**: Given a query with no resolvable taxonomic signal (e.g. "surprise me" or a qualitative-only request), when submitted, then the response is `unresolved`, and no GBIF call was made.
- **AC-003**: Given a query resolving to a valid taxon that genuinely has zero GBIF occurrences (simulated via a stubbed GBIF response in tests), when submitted, then the response is `no_results`, distinct from `unresolved`.
- **AC-004**: Given GBIF fails after the configured retries (simulated via a stubbed client), when a resolved query is submitted, then the response is `502`/`gbif_unavailable` with non-technical user copy, the failure is logged distinctly (REQ-017), and the request still counted against the daily LLM budget.
- **AC-005**: Given a caller exceeds the per-IP rate limit, when they submit another query, then the response is `429` with `error: "rate_limited"`, distinct in copy from the daily-cap case.
- **AC-006**: Given the global daily LLM-call budget has been exhausted, when any caller submits a query, then the response is `429` with `error: "daily_limit_reached"`, and no LLM call is made for that request.
- **AC-007**: Given a query longer than the configured maximum, or empty/whitespace-only, when submitted, then the response is `422` and no LLM call is made.
- **AC-008**: Given a common taxon whose total occurrence count exceeds the scale-guard threshold under the full year range, when GBIF is queried, then the year range used falls back to the single latest year (verifiable via a log line, per `WORK_SUMMARY_250726.md`'s validation approach), not the full range.
- **AC-009**: Given the app starts up, then the Anthropic API key is fetched explicitly via the Secret Manager client library and is never present as a plain environment variable in the process.
- **AC-010**: Given a user has not made a consent choice yet, when they submit a query, then the feature works normally (resolved/unresolved/etc. all function), but no `query_submitted`/`query_outcome`/`$ai_generation` PostHog events are emitted.
- **AC-011**: Given a user has accepted consent, when they submit a query resulting in a real Anthropic call, then a client-side `query_submitted`/`query_outcome` event and a server-side `$ai_generation` event both fire, sharing the same `distinct_id`.
- **AC-012**: Given the landing page loads, then the query box (not the old interest form) is the primary interactive element, and `POST /api/interest` remains reachable but unused by the frontend.
- **AC-013**: Given the Tier 1 eval case ("I want to see birds") is run (`pytest -m eval`), then it makes a real Anthropic call and asserts the resolved filter is `class/Aves`.
- **AC-014**: Given the Tier 2 eval case is run, then it makes a real Anthropic call followed by a real GBIF call and asserts a non-empty species list where every species is genuinely class Aves.
- **AC-015**: Given the default `pytest` run (no `-m eval`) or CI's `unit-tests-backend` job runs, then neither eval case executes (no real network/API calls made).
- **AC-016**: Given the deployed Cloud Run service becomes unreachable (health check failing), when REQ-025's uptime check observes the configured number of consecutive failures, then REQ-026's alert policy fires and the maintainer receives an email via the configured notification channel.
- **AC-017**: Given the deployed service starts returning an elevated rate of 5xx responses (without necessarily being fully down), when REQ-027's alert policy's threshold is crossed, then the maintainer receives a distinct email for this condition, separate from the uptime alert.

# 8. Test Strategy

Per `/tdd`/`/testing` — vertical-slice TDD (one behavior, one test, minimal code, repeat), tests through public interfaces, no 1:1 file-to-test mapping, real test doubles preferred, mocking only at the true external boundary (Anthropic client, GBIF client — both non-deterministic/networked, the textbook case for mocking per the testing skill).

**Unit/small tests** (no network, milliseconds):
- Input validation: rejects empty/whitespace, rejects over-length, accepts valid input (REQ-002, AC-007).
- Taxon resolution: cache hit for each curated rank type, cache miss falling through to a stubbed `species/match` call, `EXACT`/`FUZZY≥85`/`FUZZY<85`/`NONE` classification (REQ-008).
- Daily budget counter: allows requests under the threshold, rejects at/over it, resets on a simulated date change (REQ-004).
- Response shaping: given a stubbed LLM response and stubbed GBIF response, asserts the correct one of the four outcome shapes for each combination (resolved/unresolved/no_results/gbif_unavailable) — AC-001 through AC-004.

**Integration/medium tests** (route-level, `TestClient`, boundary-mocked Anthropic/GBIF):
- `POST /api/query` end-to-end through the route with a stubbed Anthropic client returning a known `taxonFilter`, and a stubbed GBIF client returning known occurrences — asserts full response shape and status code for each outcome (AC-001-AC-004).
- Rate-limit and daily-cap enforcement through the actual route (not the underlying counter in isolation) — AC-005, AC-006.
- Scale-guard behavior: stub GBIF's count-probe response above/below the threshold, assert the year param used on the follow-up paginated call (AC-008).
- Consent-gating: assert `$ai_generation`/AI Observability instrumentation is skipped when the request's consent flag is absent/false (AC-010), fires when present (AC-011) — mock the OTel span processor at the boundary, don't assert internal call counts on it beyond whether it was invoked.

**Eval tests** (`@pytest.mark.eval`, real network, excluded from default run/CI — REQ-024, built on DeepEval):
- Tier 1: real Anthropic call, "I want to see birds" → assert `taxonFilter == {"taxonRank": "class", "taxonValue": "Aves"}` (AC-013).
- Tier 2: real Anthropic + real GBIF call, same query → assert non-empty species list, every entry genuinely class Aves (AC-014).

**Live evaluation** (REQ-022, not a `pytest` tier — configured in PostHog, runs against real/eval traffic once REQ-019 ships): at minimum one code-based (Hog) rule checking captured `$ai_generation` outputs are well-formed. Not exercised by the backend test suite; validated by checking PostHog's evaluation results against known traffic post-deploy (§12).

**Frontend (Vitest + RTL):**
- Query box renders and submits to `/api/query` with the right payload shape (including `distinctId`).
- All four success-shaped outcomes render distinctly (resolved with species list, unresolved, no_results, gbif_unavailable).
- Both `429` cases render distinct, purpose-appropriate copy (AC-005/AC-006 as seen from the frontend).
- Consent banner's updated copy renders; query submission works identically regardless of consent state (AC-010).

# 9. Rationale & Context

**Global daily cap over pure per-IP limiting, as the primary cost control**: per-IP rate limiting alone stops one source from driving cost, but doesn't cap total spend across many sources — for a solo, non-commercial project willing to degrade the whole app's experience for a day rather than risk debilitating cost, a global ceiling matches the actual risk case directly. Both layers were kept (not either/or) since they solve different problems: per-IP stops a single fast repeat-caller quickly (seconds/minutes), the global cap bounds total daily exposure (next-day reset).

**In-memory over Firestore/GCS/Redis for both counters**: three persistent-store alternatives were explicitly designed and rejected during this slice's `/grill-me` session — Firestore (atomic, exact, near-free, but judged "a whole separate DB" of overkill for a solo project), a GCS-object-with-generation-precondition counter (genuinely precise and redeploy-proof, still judged more new infrastructure — a bucket, an IAM binding, a dependency, a ~30-40 line service module — than wanted for this slice), and pinning Cloud Run's `min_instance_count`/`max_instance_count` to 1 (doesn't actually solve it — redeploys still reset an in-memory counter even with one instance — and `min_instance_count=1` trades cost-avoidance for a real, ongoing idle-cost floor, plus it's an app-wide scaling change, not an LLM-specific one). The explicit accepted trade-off: both guardrails reset on redeploy/cold start/multi-instance, backstopped by the pre-existing Anthropic account-level billing hard-stop.

**Anthropic key via explicit Secret Manager fetch, not Cloud Run's native env-var injection**: against an attacker with code execution inside the container, the two approaches are equally exposed (Cloud Run's metadata server hands ambient service-account credentials to any code in the container either way — an attacker can call Secret Manager directly regardless of which mechanism the app itself uses; the real defense in both cases is the IAM boundary, which is identical). They diverge only against accidental, self-inflicted leaks — a plain env var can be swept into a wholesale `os.environ` dump (e.g. into this project's own `JsonLogFormatter`'s `extra=` fields, a concrete, not hypothetical, pathway) without anyone intending to touch the secret at all, where an explicitly-fetched variable requires a developer to directly reference it. This distinction was judged decisive specifically because **this project is open source** — the "just don't do that" mitigation only holds for a maintainer who was part of this design conversation, not external contributors debugging something unrelated who don't know the convention.

**PostHog AI Observability, consent-gated to match the existing client-side banner**: this server-side capture sends the actual content of user queries (and LLM output) to a third party, a materially different data-sharing decision than Cloud Logging (which stays first-party within GCP) — gating it behind the same consent choice keeps the project's privacy posture consistent rather than treating server-side observability as automatically exempt. Accepted trade-off: declined-consent users' queries get less observability than Cloud Logging alone provides; revisit if consent-opt-in rates turn out low enough to meaningfully hurt visibility (Cloud Logging's guardrail/outcome-level signal remains available either way, ungated).

**Dropping `q`, mixed-taxa, and `sort` for this slice**: the original design (`PLANNING_INTENT_QUERY_210726.md`) is a fuller pipeline than this slice needs. `q` was cut specifically because it's the one field in that design that goes semi-raw into the downstream GBIF call, relying on prompt-level instruction as its only guardrail (no code-level validation possible, unlike `taxonValue` which is checked against a fixed vocabulary) — cutting it removes the one place where "trust the model's instructions" would be the sole control, better aligned with a slice whose entire purpose is guardrails. Mixed-taxa/`sort` were cut purely for scope — this slice is about proving the guardrail pattern end-to-end, not the fuller feature.

**Unresolved → no GBIF call (not a fallback to a default search)**: the original design's fallback behavior (unresolved taxa fall through to an unfiltered default search) was considered and explicitly rejected for this slice — the product goal here is to establish and test the pattern where a GBIF call is correctly *not* made, mirroring the pre-validation step already proven in the fuller prototype's query-validation gate (PRD Story 4). The "happy path" gaining more sophistication (broader query understanding) is the intended direction for future slices, not this one.

**DeepEval for the pytest suite, PostHog AI Evals for live traffic — both kept, at different stages of the same pipeline**: these were evaluated as alternatives to a hand-rolled `pytest` eval suite and found to serve different moments, not compete for the same one. DeepEval is pytest-native (built specifically to "gate changes on LLM regression tests" under a normal test runner) and runs entirely offline against deliberately chosen test cases before code ships — the right tool for REQ-024's "does this specific query still resolve correctly" regression check. PostHog AI Evals instead runs *after* code ships, scoring real captured `$ai_generation` traffic (from REQ-019's instrumentation) continuously — the right tool for "is the live system still behaving well," including inputs no one thought to write a test case for. Given REQ-019 already wires up the OTel capture PostHog Evals depends on, turning it on is near-zero marginal setup, so there's no real cost to keeping both: DeepEval catches regressions pre-merge on known cases, PostHog Evals catches drift in production on unknown ones.

**Basic uptime/error-rate alerting, GCP-native rather than a third-party tool**: the project already has logging and is gaining two layers of LLM-specific observability this slice (REQ-017-019), but none of that is *alerting* — nothing pushes a signal to the maintainer when something breaks; today the only check that the site is actually reachable is CI's one-off post-deploy smoke test (`ADR-005`). Cloud Monitoring's uptime checks/alert policies/email notification channels are used rather than a third-party status/alerting tool (e.g. UptimeRobot, PagerDuty) because they're native to the GCP project already in place, provisionable via the same Terraform workflow as everything else in `infra/`, and proportionate to a solo maintainer who explicitly asked for "something so I can see something going wrong and act" — not a team on-call rotation. Scoped deliberately narrow (uptime + 5xx rate only) so it's a real foundation to build on (per-guardrail-trigger alerting, cost-anomaly alerting) rather than an attempt to solve comprehensive alerting in this slice.

**Two-tier eval suite, `pytest`-marked rather than a bespoke runner**: the standard TDD suite structurally cannot verify actual LLM judgment or real GBIF behavior (both non-deterministic/networked, correctly mocked at the boundary for the deterministic suite). A separate eval layer is the only way to check "does the model actually understand 'birds'" and "does the whole pipeline actually work against live GBIF" — kept as `pytest` markers (not a standalone script) specifically so it reuses existing tooling/reporting/CI wiring rather than introducing a second test framework, while staying excluded from the CI-gating run given cost and non-determinism.

# 10. Dependencies & External Integrations

- **Anthropic API** (Messages API, tool-use/structured output, `claude-sonnet-5`) — the LLM call itself.
- **GBIF public API** (`api.gbif.org/v1/occurrence/search`, `/v1/species/match`) — no auth, rate-limited by GBIF's own (undocumented) policy, hence the retry/timeout/scale-guard handling.
- **GCP Secret Manager** — Anthropic API key storage/retrieval, via the existing `backend_secret_names` scaffold.
- **PostHog** (EU-hosted, existing project) — the existing client-side product-analytics SDK, the new server-side AI Observability product (`posthog[otel]`, `opentelemetry-instrumentation-anthropic`, REQ-019), and PostHog AI Evals (REQ-022, configured within the PostHog project, no new package) as the live-traffic evaluation layer.
- **`slowapi`** — per-IP rate limiting middleware.
- **DeepEval** — open-source, pytest-native LLM evaluation framework, underlying REQ-024's offline eval suite.
- **GCP Cloud Monitoring** — uptime checks, alert policies, and email notification channel for REQ-025/026/027.

# 11. Examples & Edge Cases

- `"I want to see birds"` → `taxonFilter: {taxonRank: "class", taxonValue: "Aves"}` (curated cache hit) → likely triggers the scale guard given birds' known ~57K-occurrence volume over `2023,2026` in Retiro (`WORK_SUMMARY_250726.md`) → falls back to the single latest year for the real paginated fetch → `resolved` with a real species list.
- `"surprise me"` → no taxonomic signal → `taxonFilter: null` → `unresolved`, no GBIF call — this is the deliberately-preserved "prove the guardrail" branch (§9).
- A deliberately made-up/misspelled taxon → `species/match` returns `matchType: NONE` → `unresolved`, no GBIF call.
- A resolved, valid taxon with a live-`species/match` cache miss (not birds/insects/mammals/etc.) → falls through to a real `species/match` call, same `EXACT`/`FUZZY≥85` rule applies.
- GBIF returns a 5xx or times out on all retries → `gbif_unavailable`, logged distinctly, generic copy shown, still counted against the daily budget.
- A resolved, valid taxon with genuinely zero occurrences in Retiro over the full range → `no_results` — expected to be rare (Retiro is well-observed, high taxon levels are used, and the range spans 4 years), but must be a real tested path, not assumed unreachable (§4 REQ-012).

# 12. Validation Criteria

- All unit/integration tests (REQ-023) pass under the standard `pytest` run (no `-m eval`), and CI's existing `unit-tests-backend` job continues to pass unmodified in structure (only gains new test files).
- Both DeepEval-based eval tiers (REQ-024) pass when run manually (`pytest -m eval`) against the real Anthropic and GBIF APIs.
- REQ-022's PostHog Eval rule shows results against real captured generations in the PostHog dashboard once traffic flows post-deploy — confirm at least one evaluation run has scored real `$ai_generation` events, not just that the rule is configured.
- Manual smoke test against the deployed app (consistent with the existing post-deploy smoke-test pattern, `ADR-005`): submit a real query through the live frontend, confirm a real species list renders, confirm the consent banner's updated copy displays, confirm PostHog's dashboard shows both a client-side event and (after accepting consent) a server-side `$ai_generation` event sharing one `distinct_id`.
- Manually verify (e.g. via a log line during a real run) that the scale guard actually engages for a birds-class query, per the same verification approach used in `WORK_SUMMARY_250726.md`.
- Confirm via `gcloud`/GCP console that the Anthropic API key is never present in the running container's environment variables (`printenv` inside a shell into the container, or equivalent), only fetched into application memory at startup.
- Use Cloud Monitoring's own "send test notification" feature on the configured email channel to confirm delivery, rather than deliberately taking the production service down — reserve an actual induced-failure test (e.g. a brief manual service stop) for a planned maintenance window if end-to-end confidence beyond the test notification is wanted.

# 13. Related Specs / Further Reading

- Parent PRD: `docs/prds/nature-quest-prd-300726.md` (Slice 2).
- Prior slice: `docs/specs/spec-infrastructure-production-foundation-300726.md` (production foundation this slice builds on; REQ-013/016/017 and their `[DEFERRED]` notes are the direct precursors to REQ-005/REQ-019/REQ-020 here).
- Fuller future design this slice deliberately narrows: `docs/status_docs/PLANNING_INTENT_QUERY_210726.md`.
- Design-session checkpoint (point-in-time, superseded by this document): `docs/status_docs/PLANNING_LLM_GUARDRAILS_040826.md`.
- `docs/decisions/ADR-005` (CI/CD, WIF), `ADR-006` (secrets/environments/region), `ADR-007` (analytics/consent/abuse posture), `ADR-008` (open-source-from-day-one and its consequences for every security decision in this spec).
