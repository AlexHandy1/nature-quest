---
title: Switch Taxon Resolution to OpenRouter (Gemini 3.7 Flash) and its PostHog AI-Observability Wrapper
version: 1.0
date_created: 2026-08-28
last_updated: 2026-08-28
tags: [architecture]
status: Design complete, not yet built
sources:
  - production: app/backend/services/anthropic_client.py, app/backend/services/ai_observability.py, app/backend/services/tts.py, app/backend/routers/query.py, app/backend/services/logging_client.py, app/backend/services/rate_limiter.py, app/backend/tests/test_query_consent_wiring.py, app/backend/tests/test_query.py, app/backend/tests/evals/test_taxon_resolution_eval.py, app/backend/tests/evals/test_ai_observability_capture_eval.py, app/backend/requirements.txt, infra/secret_manager.tf
  - prototype: prototypes/scripts/model_comparison_spike.py, prototypes/scripts/server_model_comparison.py, prototypes/web/index_model_comparison.html
  - docs/status_docs/WORK_SUMMARY_280826.md, WORK_SUMMARY_210826.md
  - docs/decisions/ADR-009-posthog-ai-observability-wrapper-client.md, ADR-006-secrets-environments-region-data-handling.md, ADR-007-analytics-consent-abuse-guardrails.md, ADR-008-open-source-from-day-one.md
  - docs/prds/nature-quest-prd-300726.md (Slice 11: LLM/AI provider abstraction)
  - docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md
---

# Introduction

This spec covers the first real increment of PRD Slice 11 ("LLM/AI provider abstraction... support provider/model swapping"), scoped narrowly per explicit direction: swap the **taxon-resolution** LLM call (`POST /api/query`'s NL → GBIF taxon filter step) from Anthropic Haiku to OpenRouter's `google/gemini-3.7-flash`, and switch its PostHog AI-observability wrapper client accordingly. Narration generation (`services/narration.py`) is untouched — it stays on the Anthropic SDK directly, unaffected by this slice.

This document is meant to be implementable by an agent with no other context from this project. Read it fully before writing code.

# 1. Purpose & Scope

**In scope:**
- Switching `routers/query.py`'s taxon-resolution call from `services/anthropic_client.py`'s Anthropic-SDK path to a new OpenRouter-based path, defaulting to `google/gemini-3.7-flash`.
- Switching the PostHog AI-observability wrapper used for that call from `posthog.ai.anthropic.Anthropic` to `posthog.ai.openai.OpenAI` (pointed at OpenRouter's base URL) — same consent-gating shape as today, per ADR-009.
- Normalizing OpenAI-shaped usage/response data back into the exact shapes `routers/query.py`, `services/logging_client.py`, and `services/query_budget.py` already expect, so nothing downstream of the LLM call changes.
- A real wall-clock timeout safeguard on the new call path (see §2 — confirmed live gap in the underlying HTTP client library, not specific to this app).
- Updating the existing unit tests that currently mock an Anthropic-SDK-shaped response for this call site.

**Explicitly out of scope for this slice:**
- Narration generation (`services/narration.py`) — stays on Anthropic/Haiku directly, no model-variability work done there yet (confirmed still true as of `WORK_SUMMARY_210826.md`).
- A runtime/env-var toggle between providers. This is a straight swap via a build-time constant (see CON-001, §9) — not a live feature flag.
- Automatic fallback to Anthropic if the OpenRouter call fails at runtime. Matches this codebase's existing fail-clean convention (e.g. `routers/narration.py`'s TTS failure → 502 `tts_unavailable`, no silent retry against a different provider) — an OpenRouter failure is a failure, not a trigger to re-route through Anthropic.
- New resilience/error-handling around the LLM call itself beyond the wall-clock timeout in §4. Today, `routers/query.py` has **no explicit try/except around the Anthropic call at all** — an unhandled exception currently falls through to FastAPI's generic 500. This slice preserves that behavior; building an explicit `llm_unavailable`-style outcome (mirroring GBIF's `gbif_unavailable`) is a separate, later improvement, not required here.
- Any change to `test_taxon_resolution_eval.py`'s seeded queries/expected values, the GBIF resolution step, rate limiting, or the daily budget mechanism.
- General multi-provider abstraction across every LLM call site (full Slice 11) — this is one call site only.

# 2. Verified Facts

**PostHog's OpenAI wrapper already anticipated exactly this migration** (`ADR-009`, confirmed by reading the installed package source directly, not assumed from docs): `posthog.ai.openai.OpenAI` subclasses `openai.OpenAI` and forwards all `**kwargs` to the parent's `__init__`, so `base_url="https://openrouter.ai/api/v1"` works directly — installed and verified present at `app/backend/venv/lib/python3.13/site-packages/posthog/ai/openai/openai.py`. Per-call `posthog_distinct_id`/`posthog_properties` kwargs exist on `.chat.completions.create()`, matching the exact `posthog_distinct_id` pattern `routers/query.py`'s `_resolve_taxon_filters` already passes today.

**A real, confirmed cosmetic quirk in the resulting PostHog events**: `posthog.ai.openai.OpenAI` always sets `$ai_provider: "openai"` on captured events — it does not detect OpenRouter from `base_url` (read directly from `posthog/ai/utils.py`'s event-building code, `call_llm_and_track_usage`). The actual model and endpoint are still captured correctly (`$ai_model` = whatever's passed as `model`, e.g. `"google/gemini-3.7-flash"`; `$ai_base_url` = the real OpenRouter URL) — so this call is fully distinguishable in PostHog, just not by `$ai_provider`. See GUD-001.

**`openai` is not currently a backend dependency** — `app/backend/requirements.txt` has no `openai` entry. `posthog`'s own package only lists `openai` as an optional `test` extra (`posthog-6.9.3.dist-info/METADATA`), so `posthog.ai.openai.OpenAI` raises a clear `ModuleNotFoundError` if it's missing. `openai==2.53.0` is already present in `app/backend/venv` (verified via `find ... -iname "openai*"`), so that's the version to pin.

**`OPENROUTER_API_KEY` is already fully provisioned in production** — added to `infra/secret_manager.tf`'s `backend_secret_names` and applied live during the audio-narration slice (`WORK_SUMMARY_210826.md`). `services/tts.py::resolve_api_key()` already fetches it via the same explicit Secret-Manager-client pattern as `anthropic_client.py`'s `resolve_api_key()` (REQ-005 pattern, ADR-006), and `routers/narration.py` already imports it directly (`from services.tts import resolve_api_key as resolve_openrouter_api_key`) — this slice reuses that exact import, no new Terraform or Secret Manager work needed.

**A real, live-reproduced timeout bug exists in the underlying HTTP client layer this app would inherit**, found and fixed in this session's prototype (`prototypes/scripts/model_comparison_spike.py`, `WORK_SUMMARY_280826.md`): `httpx`'s `timeout` parameter (which `openai`'s SDK also builds on internally) bounds the gap *between* reads, not total wall-clock response time. A live call against `google/gemini-2.5-flash-lite` for the query "I want to see European robins" ran **183.8 seconds** despite a configured 60s timeout, apparently because the response kept trickling data (keep-alives) that reset the per-read timer. This was specific to that model in testing, but the underlying client-library gap is not model-specific — any OpenRouter model could in principle exhibit it. The prototype's fix (`ThreadPoolExecutor` + `future.result(timeout=...)` enforcing a real wall-clock deadline) is a directly reusable pattern for production. See REQ-006.

**Pricing, pulled directly from OpenRouter's public `/api/v1/models` endpoint** (same source for both models, so directly comparable — `WORK_SUMMARY_280826.md`): `anthropic/claude-haiku-4.5` is $1.00/M input tokens, $5.00/M output tokens. `google/gemini-3.7-flash` is $0.375/M input, $1.875/M output — **62.5% cheaper on both axes**. On one real observed query, Gemini 3.7 Flash's actual OpenRouter-reported cost was roughly **3.5x cheaper** than Haiku's equivalent cost computed from real token usage on the same query.

**Live comparison result against production's own eval, this session** (`prototypes/scripts/model_comparison_spike.py`, one run, 25 seeded queries from `test_taxon_resolution_eval.py`): `google/gemini-3.7-flash` matched expected output on **24/25** queries. The one miss: "I want to see Turtles" returned `{"taxonRank": "order", "taxonValue": "Testudines"}` instead of the expected `{"taxonRank": "class", "taxonValue": "Testudines"}` — this app's own `TAXON_GUIDANCE` documents that GBIF's backbone taxonomy has no single "Reptilia" class, so Testudines is deliberately queried at `class` rank in this app even though that's not standard biological taxonomy; Gemini 3.7 Flash defaulted to biologically-conventional "order" instead. No stalling/timeout or connection-error issues were observed for this specific model in that run (unlike `google/gemini-2.5-flash-lite` and `deepseek/deepseek-v4-flash`, which both showed separate reliability issues not relevant to this spec's chosen model). See §11 for the accepted-risk framing of this one miss.

**Existing unit tests assume the Anthropic-SDK response shape for this exact call site** (`app/backend/tests/test_query_consent_wiring.py`, `app/backend/tests/test_query.py`): mocks build `SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={...})], usage=SimpleNamespace(input_tokens=.., output_tokens=..))` and assert `client.messages.create` was called with specific kwargs. These will break under the new call shape (`client.chat.completions.create(...)`, tool call arguments as a JSON string inside `choices[0].message.tool_calls[0].function.arguments`, usage as `.prompt_tokens`/`.completion_tokens`) and must be rewritten, not just patched around.

**`TAXON_GUIDANCE` and `QUERY_SCHEMA_TOOL`'s single source of truth is `services/anthropic_client.py`.** The prototype deliberately *copied* these (not imported) per `prototypes/README.md`'s "don't import from `app/`" convention — that convention is prototype-only (stated explicitly in the `create-technical-spec` skill instructions) and does not apply to this production spec; production code should **import**, not duplicate, per GUD-002.

# 3. Definitions

- **OpenRouter**: third-party API gateway this app already integrates with for TTS (`services/tts.py`); proxies many providers' models behind one OpenAI-compatible chat-completions REST API.
- **`posthog.ai.openai.OpenAI`**: PostHog's instrumented subclass of the `openai` SDK's client — auto-captures `$ai_generation` events (input, output, tokens, latency, cost) on every `.chat.completions.create()` call, same role as `posthog.ai.anthropic.Anthropic` plays for the current Haiku call (ADR-009).
- **Wall-clock timeout**: a hard deadline on total call duration, distinct from `httpx`'s/`openai`'s own `timeout` parameter, which only bounds gaps between individual reads (see §2).
- **`$ai_*` properties**: PostHog's reserved event property names for AI observability (`$ai_provider`, `$ai_model`, `$ai_base_url`, `$ai_input_tokens`, etc.), auto-populated by the wrapper client.

# 4. Requirements, Constraints & Guidelines

**Call-site swap**
- **REQ-001**: `routers/query.py`'s `_resolve_taxon_filters` calls a new OpenRouter-based resolution function instead of `services.anthropic_client.resolve_taxon_filters`, defaulting to model `google/gemini-3.7-flash`.
- **REQ-002**: A new module, `services/openrouter_taxon_client.py`, provides `resolve_taxon_filters(query: str, client, on_response=None, **extra_kwargs) -> list[dict]` — same name and signature shape as `anthropic_client.py`'s existing function, so `routers/query.py`'s call site changes only its import and the client-construction call, not its own logic.
- **REQ-003**: `services/openrouter_taxon_client.py` imports `TAXON_GUIDANCE` and `QUERY_SCHEMA_TOOL` from `services.anthropic_client` (not a copy) and derives the OpenAI/OpenRouter tool-calling shape from them at import time (mirrors the prototype's `QUERY_SCHEMA_TOOL_OPENAI` construction — same `{"type": "function", "function": {...}}` wrapper around the existing schema).
- **REQ-004**: The OpenRouter call uses forced tool choice (`tool_choice: {"type": "function", "function": {"name": "produce_gbif_query"}}`), matching the exact forcing behavior `anthropic_client.py`'s `tool_choice={"type": "tool", "name": "produce_gbif_query"}` already provides — verified equivalent in the prototype across all 25 seeded queries.
- **REQ-005**: `services/openrouter_taxon_client.py` extracts `taxonFilters` from `response.choices[0].message.tool_calls[0].function.arguments` (a JSON string requiring `json.loads`), and raises/propagates clearly (not silently returns `[]`) if no tool call is present or the arguments don't parse — matching this codebase's existing "fail loud, don't silently substitute a different outcome" posture (`spec-tool-llm-guardrails-gbif-query-040826.md` REQ-008's rationale for the `unresolved` branch applies by the same logic here: a malformed response is not the same thing as a genuinely empty `taxonFilters: []`).

**Observability**
- **REQ-006**: `services/ai_observability.py`'s `build_client` gains the ability to build an OpenRouter-bound client: given `consent=True`, returns `posthog.ai.openai.OpenAI(api_key=..., base_url="https://openrouter.ai/api/v1", posthog_client=...)`; given `consent=False`, returns a plain `openai.OpenAI(api_key=..., base_url=...)`. Same consent-gating shape as the existing Anthropic path — implementer's choice whether this is a new parameter on the existing `build_client` (e.g. `provider: Literal["anthropic", "openrouter"]`) or a sibling function (`build_openrouter_client`); either is acceptable as long as the consent-gating logic itself is not duplicated inline in `routers/query.py`.
- **REQ-007**: `OPENROUTER_API_KEY` resolution reuses `services.tts.resolve_api_key` (imported as `routers/narration.py` already does: `from services.tts import resolve_api_key as resolve_openrouter_api_key`) — no new Secret Manager plumbing, per the Verified Facts above.
- **REQ-008**: Per-call `posthog_distinct_id` passthrough is preserved exactly as today — `routers/query.py`'s `_resolve_taxon_filters` still builds `extra_kwargs = {"posthog_distinct_id": distinct_id} if consent else {}` and passes it through unchanged; only the underlying client type changes.
- **REQ-009**: Token-usage capture (`on_response` callback building the `usage` dict logged via `log_query_outcome`) is normalized to the exact same `{"input_tokens": int, "output_tokens": int}` shape already produced today — reading `response.usage.prompt_tokens`/`response.usage.completion_tokens` (OpenAI shape) instead of `response.usage.input_tokens`/`response.usage.output_tokens` (Anthropic shape), with no changes needed to `services/logging_client.py` or anything downstream of the `usage` dict.

**Reliability**
- **REQ-010**: The OpenRouter call in production must enforce a real wall-clock timeout, not rely solely on `openai`'s/`httpx`'s own `timeout` parameter — per the confirmed live gap in §2. Implementer's choice of exact mechanism (the prototype's `ThreadPoolExecutor` + `future.result(timeout=...)` pattern is directly reusable) and exact deadline value; **CON-002 applies** — the specific number is a build-time constant, not mandated here, chosen with `POST /api/query`'s existing `10/minute` rate limit and realistic end-user wait tolerance in mind.
- **REQ-011**: On a wall-clock timeout or any other call failure, this slice does not add new outcome-branch handling (see §1 non-goals) — the exception propagates the same way an Anthropic-call failure would today (unhandled → generic 500). If a future slice wants an explicit `llm_unavailable` outcome (GBIF-unavailable-style), that is separate follow-on work.

**Dependencies & config**
- **REQ-012**: `app/backend/requirements.txt` gains `openai==2.53.0`.
- **CON-001**: No runtime/env-var provider toggle. This is a build-time constant swap (the model string in `services/openrouter_taxon_client.py`), following this exact codebase's own precedent for the same call site (`spec-tool-llm-guardrails-gbif-query-040826.md` REQ-006: the earlier Sonnet→Haiku swap was also a redeploy-time constant change, not a live flag) and consistent with this project's stated default to avoid feature flags absent a concrete need for one.
- **CON-002 (security/disclosure convention, per `spec-tool-llm-guardrails-gbif-query-040826.md`'s established pattern)**: the exact wall-clock timeout value (REQ-010) is not mandated in this document — a build-time constant, chosen at implementation time.
- **GUD-001**: When building PostHog dashboards/insights for this call site, filter or group by `$ai_model` (`"google/gemini-3.7-flash"`) or `$ai_base_url`, not `$ai_provider` — the latter will read `"openai"` regardless of the real upstream model, per the confirmed quirk in §2. This matters if OpenAI is ever added as a *direct* provider later (not via OpenRouter) — the two would otherwise be indistinguishable by `$ai_provider` alone.
- **GUD-002**: `services/openrouter_taxon_client.py` **imports** `TAXON_GUIDANCE`/`QUERY_SCHEMA_TOOL` from `services.anthropic_client` rather than duplicating them — the prototype's copy-not-import convention was explicitly scoped to throwaway prototype code (`prototypes/README.md`) and does not apply to this production module.
- **PAT-001**: `services/anthropic_client.py` and its `resolve_taxon_filters` are left fully intact, unused by `routers/query.py` after this change but still imported by `app/backend/tests/evals/test_ai_observability_capture_eval.py` and available as a rollback reference (swap the constant back) — do not delete it as part of this slice.

# 5. Interfaces & Data Contracts

**OpenRouter chat-completions request shape** (verified live via the prototype against `google/gemini-3.7-flash`):

```json
{
  "model": "google/gemini-3.7-flash",
  "messages": [
    {"role": "system", "content": "<TAXON_GUIDANCE>"},
    {"role": "user", "content": "<query>"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "produce_gbif_query",
        "description": "...",
        "parameters": { "...": "same input_schema as QUERY_SCHEMA_TOOL" }
      }
    }
  ],
  "tool_choice": {"type": "function", "function": {"name": "produce_gbif_query"}}
}
```

**Real captured response shape** (from the prototype's live smoke test, `google/gemini-3.7-flash`, query "I want to see birds"):

```json
{
  "choices": [
    {
      "message": {
        "tool_calls": [
          {
            "function": {
              "name": "produce_gbif_query",
              "arguments": "{\"taxonFilters\": [{\"taxonRank\": \"class\", \"taxonValue\": \"Aves\"}]}"
            }
          }
        ]
      }
    }
  ],
  "usage": {
    "prompt_tokens": "<int>",
    "completion_tokens": "<int>",
    "cost": 0.000417
  }
}
```

`taxonFilters` extraction: `json.loads(response.choices[0].message.tool_calls[0].function.arguments)["taxonFilters"]`. Note key order inside each filter dict is not guaranteed (`{"taxonValue": "Aves", "taxonRank": "class"}` was observed as often as the reverse) — irrelevant since downstream code (`_resolve_taxon_keys`) accesses by key, not by dict equality, and Python dict equality is key-order-independent regardless.

**Normalized usage dict** (unchanged contract, `routers/query.py`'s `_capture_usage`):

```python
usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
```

# 6. Implementation Mechanics

**New file**: `app/backend/services/openrouter_taxon_client.py`
- Imports `TAXON_GUIDANCE`, `QUERY_SCHEMA_TOOL` (Anthropic-shaped, existing) from `services.anthropic_client`.
- `MODEL = "google/gemini-3.7-flash"` (build-time constant, CON-001).
- `QUERY_SCHEMA_TOOL_OPENAI` — module-level, built once from `QUERY_SCHEMA_TOOL` (same wrapping the prototype already validated).
- `resolve_taxon_filters(query, client, on_response=None, **extra_kwargs) -> list[dict]` — calls `client.chat.completions.create(model=MODEL, messages=[...], tools=[QUERY_SCHEMA_TOOL_OPENAI], tool_choice={...}, **extra_kwargs)`, parses per §5, calls `on_response(response)` if provided (same calling convention `routers/query.py` already relies on).
- Wall-clock timeout wrapper (REQ-010) — reuse the prototype's `ThreadPoolExecutor`-based pattern (`prototypes/scripts/model_comparison_spike.py`'s `resolve_via_openrouter`), adapted to raise/propagate rather than return an error dict (this module's contract is "return `list[dict]` or raise," matching `anthropic_client.py`'s existing contract, not the prototype's own richer diagnostic-result shape).

**Modified**: `app/backend/services/ai_observability.py`
- Add the OpenRouter/OpenAI client-construction branch (REQ-006). Keep the existing Anthropic branch untouched (still used by `narration.py`'s call site and available as rollback).

**Modified**: `app/backend/routers/query.py`
- `_resolve_taxon_filters`: swap the import (`from services.openrouter_taxon_client import resolve_taxon_filters`) and the `ai_observability.build_client(...)` call to request the OpenRouter-bound client with `api_key=resolve_openrouter_api_key()` (REQ-007) instead of `resolve_api_key()` (Anthropic).
- `_capture_usage`: read `.prompt_tokens`/`.completion_tokens` instead of `.input_tokens`/`.output_tokens` (REQ-009).

**Modified**: `app/backend/requirements.txt`
- Add `openai==2.53.0` (REQ-012).

**Updated tests**: `app/backend/tests/test_query_consent_wiring.py`, relevant cases in `app/backend/tests/test_query.py` — rewrite mocks to the OpenAI-shaped response contract (§5), per REQ described in Verified Facts.

# 7. Acceptance Criteria

- **AC-001**: Given `POST /api/query` with a resolvable free-text query, when the request is handled, then the taxon filters returned come from a real OpenRouter call to `google/gemini-3.7-flash`, not Anthropic Haiku — verifiable via a live smoke test and by inspecting the PostHog event's `$ai_model`.
- **AC-002**: Given `consent=True`, when `_resolve_taxon_filters` runs, then a `$ai_generation` PostHog event is captured with `$ai_model: "google/gemini-3.7-flash"` and `$ai_base_url` containing `openrouter.ai` — verifiable via `test_ai_observability_capture_eval.py`-style live check (see §8) and manually in the PostHog dashboard (carries over the still-open item from `WORK_SUMMARY_210826.md`: no one has yet visually confirmed a narration-equivalent trace in the dashboard — this AC extends that same manual confirmation to the taxon-resolution call).
- **AC-003**: Given `consent=False`, when `_resolve_taxon_filters` runs, then no PostHog event is captured and no `posthog_distinct_id`-shaped kwarg reaches the OpenRouter call — same behavior as today's Anthropic path, verified by an updated version of `test_query_consent_wiring.py`'s existing `test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs`.
- **AC-004**: Given `test_taxon_resolution_eval.py` run against the new production call path (not the old Anthropic path), then **[NEEDS INPUT: is 24/25 an acceptable bar to ship, given the one known miss (Turtles, §2/§11) is a real behavior change from today's 25/25-passing Haiku baseline? Options: (a) ship accepting this as a known regression on one query, (b) tighten `TAXON_GUIDANCE`'s reptile-rank wording specifically to close this gap before shipping, (c) something else.]** This spec does not decide that trade-off — flagging it here rather than silently picking one.
- **AC-005**: Given the OpenRouter call stalls past the wall-clock timeout (REQ-010), when the timeout fires, then the request fails within the configured deadline rather than hanging indefinitely — verifiable by a unit test that mocks a slow/never-returning call and asserts the timeout path is hit.
- **AC-006**: Given the same query submitted twice, when compared to Haiku's current behavior on all queries *except* the known Turtles gap, then output is unchanged in shape and correctness — i.e. this is a cost/provider swap, not a behavior regression beyond the one flagged exception.

# 8. Test Strategy

Full TDD per `/tdd`/`/testing` — this is production code, not prototype code.

**Unit tests** (mocked, no real API calls, red-green-refactor):
- `services/openrouter_taxon_client.py`: new test file mirroring the shape of tests already covering `anthropic_client.py`'s `resolve_taxon_filters` (if any exist at that granularity — check first) or, if none exist at that layer today, at minimum cover: (a) a well-formed tool-call response parses to the expected `taxonFilters`; (b) a response with no tool call raises/propagates rather than returning `[]` silently (REQ-005); (c) malformed JSON in the tool-call arguments raises/propagates; (d) the wall-clock timeout path (REQ-010) is exercised with a mocked slow call and asserted to fail within the configured deadline, not the real one.
- `app/backend/tests/test_query_consent_wiring.py`: rewrite all three existing tests (`test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs`, `test_consent_true_builds_an_observed_client_and_passes_distinct_id`, `test_returns_the_taxon_filter_and_token_usage`) to mock the OpenAI-shaped `client.chat.completions.create` return value (§5's response shape) instead of the Anthropic-shaped one currently mocked.
- `app/backend/tests/test_query.py`: any test patching `routers.query._resolve_taxon_filters` directly is unaffected (mocks the function boundary, not the client shape) — verify this holds; only tests reaching into the client-construction/response-parsing layer need updates.
- `services/ai_observability.py`: new test(s) covering the OpenRouter/OpenAI branch of `build_client` (or the new sibling function per REQ-006's implementer's-choice) — consent True/False, correct `base_url`, correct wrapper class used.

**Eval tier** (real API calls, not CI-gating):
- `app/backend/tests/evals/test_taxon_resolution_eval.py`: update its imports to call the new production entrypoint (`services.openrouter_taxon_client.resolve_taxon_filters` via `routers/query.py`'s actual construction path, or the module directly — implementer's choice, but it must exercise the real new code path, not remain pointed at `anthropic_client.py`) so this eval is testing prod's actual behavior going forward, per AC-004's resolution.
- `app/backend/tests/evals/test_ai_observability_capture_eval.py`: add or extend a case for the OpenRouter path (consent=True → real `$ai_generation` event with `$ai_model` = the new model), following the exact existing pattern (`REQUIRES_POSTHOG` skip guard, explicit `.flush()` before asserting, per ADR-009's noted delivery-confirmation requirement).

# 9. Rationale & Context

This slice exists because: (a) PRD Slice 11 explicitly scopes "LLM/AI provider abstraction... support provider/model swapping" as a planned post-MVP optimisation, and (b) this session's prototype work (`prototypes/scripts/model_comparison_spike.py`, `WORK_SUMMARY_280826.md`) found a concrete, verified case for it — `google/gemini-3.7-flash` is materially cheaper (62.5% per-token, ~3.5x on one real observed call) with only one known behavioral miss out of 25 real production eval queries, and no reliability/timeout issues in that run (unlike the other three candidates tested, which all showed real correctness or reliability problems and were rejected as the choice for this slice).

The build-as-constant-not-flag decision (CON-001) follows this codebase's own established precedent: the earlier Sonnet→Haiku swap for this identical call site (`spec-tool-llm-guardrails-gbif-query-040826.md` REQ-006) was also a redeploy-time constant change, not a live toggle — consistent with CLAUDE.md's general guidance to avoid feature flags absent a concrete need, and this project's current solo/pre-launch traffic profile doesn't yet demand live provider switching.

The wall-clock-timeout requirement (REQ-010) is included specifically because it was *found*, not theorized — a live production-adjacent reproduction in this session's prototype work, not a speculative "what if" risk.

# 10. Dependencies & External Integrations

- **OpenRouter**: already an integrated third-party dependency (`services/tts.py`) — this slice adds a second call site (chat completions) against the same account/API key, no new vendor relationship.
- **`OPENROUTER_API_KEY`**: already provisioned in GCP Secret Manager and Terraform (`infra/secret_manager.tf`), no new infra work.
- **`openai` Python package**: new dependency (REQ-012), required transitively by `posthog.ai.openai.OpenAI`.
- **`posthog` Python package**: already a dependency (`posthog==6.9.3`), already includes the `posthog.ai.openai` module — no version bump needed (verified: the module is present in the currently-pinned version).

# 11. Examples & Edge Cases

- **Known accepted-or-rejected risk (AC-004, [NEEDS INPUT])**: "I want to see Turtles" → Gemini 3.7 Flash returns `taxonRank: "order"` instead of this app's deliberately non-standard `taxonRank: "class"` for Testudines (§2). This is the one concrete behavior difference from today's Haiku baseline found in live testing — every other query in the 25-case seeded eval matched exactly, including all negation cases, the 7-entry fish expansion, the 4-class reptile expansion, and multi-taxa ordering.
- **Wall-clock stall (REQ-010)**: confirmed live on a *different* candidate model (`google/gemini-2.5-flash-lite`, not the one chosen for this slice) during prototyping — included here as a required safeguard because the underlying client-library gap (`httpx` timeout bounding per-read gaps, not total time) is not specific to that model and could in principle recur with `google/gemini-3.7-flash` too; there's no evidence it has, only evidence the mechanism exists.
- **Dict key-order variance**: OpenRouter/Gemini responses were observed returning `{"taxonValue": ..., "taxonRank": ...}` as often as the reverse key order — confirmed harmless (§5), noted here only so it isn't mistaken for a bug during implementation review.

# 12. Validation Criteria

- All unit tests (§8) pass, including the rewritten Anthropic→OpenAI-shaped mocks.
- `test_taxon_resolution_eval.py` run live against the new production path — result matches whatever AC-004 resolves to (24/25 or 25/25, depending on that decision).
- A live smoke test against a local/staging `POST /api/query` call, confirming a real OpenRouter round-trip and a correct `taxonFilters` response — per this project's general convention (CLAUDE.md) of validating production coding work end-to-end, not just via unit tests.
- Manual confirmation in the PostHog dashboard of a real `$ai_generation` event for this call site with `$ai_model: "google/gemini-3.7-flash"` (AC-002) — extends the still-open PostHog-dashboard-confirmation item already carried over from `WORK_SUMMARY_210826.md` for narration.
- `ruff`/`mypy` clean (per this project's established pre-push habit, `WORK_SUMMARY_210826.md`'s CI-failure lesson: run both locally before pushing, since CI runs both and local `.env` state can mask a real local/CI environment gap).

# 13. Related Specs / Further Reading

- `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md` — the original taxon-resolution slice this one modifies; §2/§4 REQ-006-008 describe the call site and secret-handling pattern this spec follows.
- `docs/decisions/ADR-009-posthog-ai-observability-wrapper-client.md` — directly predicted and validated the `posthog.ai.openai.OpenAI(base_url=...)` migration path this spec implements.
- `docs/decisions/ADR-006-secrets-environments-region-data-handling.md` — the explicit-Secret-Manager-fetch pattern `OPENROUTER_API_KEY` already follows.
- `docs/prds/nature-quest-prd-300726.md` — Slice 11 (LLM/AI provider abstraction), the parent PRD item this spec is the first increment of.
- `docs/status_docs/WORK_SUMMARY_280826.md` — this session's prototype work, comparison results, and the wall-clock timeout discovery this spec is built on.
- `docs/status_docs/WORK_SUMMARY_210826.md` — narration slice; source of the still-open "no one has visually confirmed a PostHog trace" item this spec's AC-002 extends.
- `prototypes/scripts/model_comparison_spike.py`, `prototypes/scripts/server_model_comparison.py`, `prototypes/web/index_model_comparison.html` — the prototype this spec's REQ-001-005 and REQ-010 are directly derived from.
