---
title: Switch Taxon Resolution to a Model-Agnostic OpenRouter Client (initial model: Gemini 3.7 Flash) and its PostHog AI-Observability Wrapper
version: 1.1
date_created: 2026-08-28
last_updated: 2026-08-30
tags: [architecture]
status: Design complete, not yet built
sources:
  - production: app/backend/services/anthropic_client.py, app/backend/services/ai_observability.py, app/backend/services/tts.py, app/backend/routers/query.py, app/backend/services/logging_client.py, app/backend/services/rate_limiter.py, app/backend/tests/test_query_consent_wiring.py, app/backend/tests/test_query.py, app/backend/tests/evals/test_taxon_resolution_eval.py, app/backend/tests/evals/test_full_pipeline_eval.py, app/backend/tests/evals/test_ai_observability_capture_eval.py, app/backend/requirements.txt, infra/secret_manager.tf
  - prototype: prototypes/scripts/model_comparison_spike.py, prototypes/scripts/server_model_comparison.py, prototypes/web/index_model_comparison.html
  - docs/status_docs/WORK_SUMMARY_280826.md, WORK_SUMMARY_210826.md
  - docs/decisions/ADR-009-posthog-ai-observability-wrapper-client.md, ADR-006-secrets-environments-region-data-handling.md, ADR-007-analytics-consent-abuse-guardrails.md, ADR-008-open-source-from-day-one.md
  - docs/prds/nature-quest-prd-300726.md (Slice 11: LLM/AI provider abstraction)
  - docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md
---

# Introduction

This spec covers the first real increment of PRD Slice 11 ("LLM/AI provider abstraction... support provider/model swapping"), scoped narrowly per explicit direction: move the **taxon-resolution** LLM call (`POST /api/query`'s NL → GBIF taxon filter step) off the Anthropic SDK onto a **model-agnostic OpenRouter client**, and switch its PostHog AI-observability wrapper client accordingly. Narration generation (`services/narration.py`) is untouched — it stays on the Anthropic SDK directly, unaffected by this slice.

**The point of this slice is the abstraction, not the model.** The new OpenRouter path must be written so that changing which model handles taxon resolution is a one-line build-time constant change with no other code edits — no model-specific parsing, prompt branching, or model-name checks anywhere. `google/gemini-3.7-flash` is the initial selection (cheapest credible option found in this session's prototype comparison — see §2/§9), not a hard-coded assumption.

This document is meant to be implementable by an agent with no other context from this project. Read it fully before writing code.

## Build steps at a glance

Full detail is in §4–§8; this is the shape of the work upfront.

1. **`requirements.txt`**: add `openai==2.53.0` (REQ-012).
2. **New `services/openrouter_taxon_client.py`**: `MODEL` constant + `resolve_taxon_filters(query, client, on_response=None, **extra_kwargs) -> list[dict]` — same signature as `anthropic_client.py`'s function. Imports `TAXON_GUIDANCE`/`QUERY_SCHEMA_TOOL` from `services.anthropic_client` (no copy), builds the OpenAI tool shape once at import, forces the tool call, parses `choices[0].message.tool_calls[0].function.arguments`, calls `on_response`, passes a plain `timeout=` constant. **No model-specific logic** (REQ-014).
3. **`services/ai_observability.py`**: extend `build_client` with an OpenRouter/OpenAI branch, consent-gated exactly like the existing Anthropic branch (REQ-006). Anthropic branch left intact.
4. **`routers/query.py`**: `_resolve_taxon_filters` swaps its import and client construction to the OpenRouter-bound client keyed by `resolve_openrouter_api_key()` (REQ-007); `_capture_usage` reads `.prompt_tokens`/`.completion_tokens` (REQ-009).
5. **Unit tests**: rewrite the Anthropic-SDK-shaped mocks in `test_query_consent_wiring.py` / `test_query.py` to the OpenAI response shape (§5). Keep one Anthropic-shaped test covering `anthropic_client.resolve_taxon_filters` directly, so the documented rollback stays verified (REQ-015).
6. **Evals must all pass before production** (REQ-013): point `test_taxon_resolution_eval.py` and `test_full_pipeline_eval.py` at the new path and run both. If any case fails — including "I want to see Turtles" → `class`/`Testudines` — iterate on `TAXON_GUIDANCE`'s wording (a prompt edit; eval expectations do **not** change) until every case passes.
7. **`test_ai_observability_capture_eval.py`**: add/extend a case for the OpenRouter path.
8. **Validate**: live smoke test of `POST /api/query`, manual PostHog `$ai_generation` confirmation, `ruff`/`mypy` clean.

# 1. Purpose & Scope

**In scope:**
- Switching `routers/query.py`'s taxon-resolution call from `services/anthropic_client.py`'s Anthropic-SDK path to a new, **model-agnostic** OpenRouter-based path, with `google/gemini-3.7-flash` as the initial model constant.
- Writing that path so a future model swap is a one-line constant change and nothing else (REQ-014).
- Switching the PostHog AI-observability wrapper used for that call from `posthog.ai.anthropic.Anthropic` to `posthog.ai.openai.OpenAI` (pointed at OpenRouter's base URL) — same consent-gating shape as today, per ADR-009.
- Normalizing OpenAI-shaped usage/response data back into the exact shapes `routers/query.py`, `services/logging_client.py`, and `services/query_budget.py` already expect, so nothing downstream of the LLM call changes.
- Editing `TAXON_GUIDANCE`'s wording as needed so the new model passes **every** existing eval case before production (REQ-013) — including the Turtles case that missed in prototyping (§2). Eval expectations are not changed; only the prompt.
- Setting `openai`'s built-in request `timeout=` to a sensible build-time constant on the new call path (REQ-010).
- Updating the existing unit tests that currently mock an Anthropic-SDK-shaped response for this call site, while keeping one test that still covers `anthropic_client.resolve_taxon_filters` directly for rollback safety (REQ-015).

**Explicitly out of scope for this slice:**
- Narration generation (`services/narration.py`) — stays on Anthropic/Haiku directly, no model-variability work done there yet (confirmed still true as of `WORK_SUMMARY_210826.md`).
- A runtime/env-var toggle between providers. This is a straight swap via a build-time constant (see CON-001, §9) — not a live feature flag. (Model-agnostic ≠ runtime-switchable: the code is written so the constant is trivially changed, but changing it is still a redeploy.)
- Automatic runtime fallback to Anthropic if the OpenRouter call fails. Matches this codebase's existing fail-clean convention (e.g. `routers/narration.py`'s TTS failure → 502 `tts_unavailable`, no silent retry against a different provider) — an OpenRouter failure is a failure, not a trigger to re-route through Anthropic. The Anthropic path is retained only as a **manual, redeploy-time rollback** (PAT-001) and must stay test-covered (REQ-015).
- A bespoke wall-clock-enforcement mechanism (`ThreadPoolExecutor` + `future.result(timeout=...)` or similar). The 183s stall in §2 was on a *rejected* free model, not the chosen one; a plain `timeout=` value is sufficient here — see REQ-010.
- New resilience/error-handling around the LLM call itself beyond that `timeout=` value. Today, `routers/query.py` has **no explicit try/except around the Anthropic call at all** — an unhandled exception currently falls through to FastAPI's generic 500. This slice preserves that behavior; building an explicit `llm_unavailable`-style outcome (mirroring GBIF's `gbif_unavailable`) is a separate, later improvement, not required here.
- Any change to the seeded queries/expected values in `test_taxon_resolution_eval.py` or `test_full_pipeline_eval.py`, the GBIF resolution step, rate limiting, or the daily budget mechanism.
- General multi-provider abstraction across every LLM call site (full Slice 11) — this is one call site only.

# 2. Verified Facts

**PostHog's OpenAI wrapper already anticipated exactly this migration** (`ADR-009`, confirmed by reading the installed package source directly, not assumed from docs): `posthog.ai.openai.OpenAI` subclasses `openai.OpenAI` and forwards all `**kwargs` to the parent's `__init__`, so `base_url="https://openrouter.ai/api/v1"` works directly — installed and verified present at `app/backend/venv/lib/python3.13/site-packages/posthog/ai/openai/openai.py`. Per-call `posthog_distinct_id`/`posthog_properties` kwargs exist on `.chat.completions.create()`, matching the exact `posthog_distinct_id` pattern `routers/query.py`'s `_resolve_taxon_filters` already passes today.

**A real, confirmed cosmetic quirk in the resulting PostHog events**: `posthog.ai.openai.OpenAI` always sets `$ai_provider: "openai"` on captured events — it does not detect OpenRouter from `base_url` (read directly from `posthog/ai/utils.py`'s event-building code, `call_llm_and_track_usage`). The actual model and endpoint are still captured correctly (`$ai_model` = whatever's passed as `model`, e.g. `"google/gemini-3.7-flash"`; `$ai_base_url` = the real OpenRouter URL) — so this call is fully distinguishable in PostHog, just not by `$ai_provider`. See GUD-001.

**`openai` is not currently a backend dependency** — `app/backend/requirements.txt` has no `openai` entry. `posthog`'s own package only lists `openai` as an optional `test` extra (`posthog-6.9.3.dist-info/METADATA`), so `posthog.ai.openai.OpenAI` raises a clear `ModuleNotFoundError` if it's missing. `openai==2.53.0` is already present in `app/backend/venv` (verified via `find ... -iname "openai*"`), so that's the version to pin.

**`OPENROUTER_API_KEY` is already fully provisioned in production** — added to `infra/secret_manager.tf`'s `backend_secret_names` and applied live during the audio-narration slice (`WORK_SUMMARY_210826.md`). `services/tts.py::resolve_api_key()` already fetches it via the same explicit Secret-Manager-client pattern as `anthropic_client.py`'s `resolve_api_key()` (REQ-005 pattern, ADR-006), and `routers/narration.py` already imports it directly (`from services.tts import resolve_api_key as resolve_openrouter_api_key`) — this slice reuses that exact import, no new Terraform or Secret Manager work needed.

**A per-read timeout quirk was observed in prototyping — on a rejected model, and not something this slice needs to engineer around.** In this session's prototype (`prototypes/scripts/model_comparison_spike.py`, `WORK_SUMMARY_280826.md`), a live call against `google/gemini-2.5-flash-lite` (one of the *free* models being compared, **not** the model chosen for this slice) for "I want to see European robins" ran **183.8 seconds** despite a configured 60s timeout — `httpx`'s `timeout` (which `openai` builds on) bounds the gap between reads, not total response time, and the model kept trickling keep-alive data that reset the per-read timer. `google/gemini-3.7-flash` showed **no such behavior** across the full 25-query eval run. The takeaway for this slice is modest: set a plain `timeout=` constant on the client (REQ-010) and move on. A bespoke wall-clock mechanism is deliberately *not* in scope (§1) — it was a prototyping workaround for free-tier flakiness, not a production requirement for the chosen model. If a future model swap surfaces the same trickle-stall, revisit it then.

**Pricing, pulled directly from OpenRouter's public `/api/v1/models` endpoint** (same source for both models, so directly comparable — `WORK_SUMMARY_280826.md`): `anthropic/claude-haiku-4.5` is $1.00/M input tokens, $5.00/M output tokens. `google/gemini-3.7-flash` is $0.375/M input, $1.875/M output — **62.5% cheaper on both axes**. On one real observed query, Gemini 3.7 Flash's actual OpenRouter-reported cost was roughly **3.5x cheaper** than Haiku's equivalent cost computed from real token usage on the same query.

**Live comparison result against production's own eval, this session** (`prototypes/scripts/model_comparison_spike.py`, one run, 25 seeded queries from `test_taxon_resolution_eval.py`): `google/gemini-3.7-flash` matched expected output on **24/25** queries. The one miss: "I want to see Turtles" returned `{"taxonRank": "order", "taxonValue": "Testudines"}` instead of the expected `{"taxonRank": "class", "taxonValue": "Testudines"}` — this app's own `TAXON_GUIDANCE` documents that GBIF's backbone taxonomy has no single "Reptilia" class, so Testudines is deliberately queried at `class` rank in this app even though that's not standard biological taxonomy; Gemini 3.7 Flash defaulted to biologically-conventional "order" instead. **This gap must be closed within this slice** (REQ-013 / AC-004) by tightening `TAXON_GUIDANCE`'s Testudines-rank wording — a prompt edit, not an eval-expectation edit. This slice does not ship to production with a failing eval case. No stalling/timeout or connection-error issues were observed for this specific model in that run (unlike `google/gemini-2.5-flash-lite` and `deepseek/deepseek-v4-flash`, which both showed separate reliability issues not relevant to this spec's chosen model).

**Existing unit tests assume the Anthropic-SDK response shape for this exact call site** (`app/backend/tests/test_query_consent_wiring.py`, `app/backend/tests/test_query.py`): mocks build `SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={...})], usage=SimpleNamespace(input_tokens=.., output_tokens=..))` and assert `client.messages.create` was called with specific kwargs. These will break under the new call shape (`client.chat.completions.create(...)`, tool call arguments as a JSON string inside `choices[0].message.tool_calls[0].function.arguments`, usage as `.prompt_tokens`/`.completion_tokens`) and must be rewritten, not just patched around.

**`TAXON_GUIDANCE` and `QUERY_SCHEMA_TOOL`'s single source of truth is `services/anthropic_client.py`.** The prototype deliberately *copied* these (not imported) per `prototypes/README.md`'s "don't import from `app/`" convention — that convention is prototype-only (stated explicitly in the `create-technical-spec` skill instructions) and does not apply to this production spec; production code should **import**, not duplicate, per GUD-002.

# 3. Definitions

- **OpenRouter**: third-party API gateway this app already integrates with for TTS (`services/tts.py`); proxies many providers' models behind one OpenAI-compatible chat-completions REST API.
- **`posthog.ai.openai.OpenAI`**: PostHog's instrumented subclass of the `openai` SDK's client — auto-captures `$ai_generation` events (input, output, tokens, latency, cost) on every `.chat.completions.create()` call, same role as `posthog.ai.anthropic.Anthropic` plays for the current Haiku call (ADR-009).
- **Wall-clock timeout**: a hard deadline on total call duration, distinct from `httpx`'s/`openai`'s own `timeout` parameter, which only bounds gaps between individual reads (see §2).
- **`$ai_*` properties**: PostHog's reserved event property names for AI observability (`$ai_provider`, `$ai_model`, `$ai_base_url`, `$ai_input_tokens`, etc.), auto-populated by the wrapper client.

# 4. Requirements, Constraints & Guidelines

**Call-site swap**
- **REQ-001**: `routers/query.py`'s `_resolve_taxon_filters` calls a new OpenRouter-based resolution function instead of `services.anthropic_client.resolve_taxon_filters`. The model is a single build-time constant in the new module, initially `google/gemini-3.7-flash` (REQ-014).
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
- **REQ-010**: The new call path sets `openai`'s built-in request `timeout=` to a sensible build-time constant (CON-002 applies — the specific number is chosen at implementation time, mindful of `POST /api/query`'s `10/minute` rate limit and realistic end-user wait tolerance). No bespoke wall-clock-enforcement mechanism is required or wanted — the 183s stall in §2 was on a rejected free model, and `google/gemini-3.7-flash` showed no trickle-stall behavior across the full eval run. Revisit only if a future model swap surfaces it.
- **REQ-011**: On a timeout or any other call failure, this slice does not add new outcome-branch handling (see §1 non-goals) — the exception propagates the same way an Anthropic-call failure would today (unhandled → generic 500). If a future slice wants an explicit `llm_unavailable` outcome (GBIF-unavailable-style), that is separate follow-on work.

**Evals & model-agnostic construction**
- **REQ-013**: Before this slice ships to production, **every existing eval case must pass on the new call path** — the full seeded set in `app/backend/tests/evals/test_taxon_resolution_eval.py` (all 25, including "I want to see Turtles" → `{"taxonRank": "class", "taxonValue": "Testudines"}`) *and* `app/backend/tests/evals/test_full_pipeline_eval.py`. The Turtles miss from prototyping (§2) is closed here by tightening `TAXON_GUIDANCE`'s Testudines/reptile-rank wording in `services/anthropic_client.py` — a prompt edit. The seeded queries and expected values in both eval files must **not** be changed to accommodate the new model; the prompt adapts to the evals, not the reverse. Expect to iterate on `TAXON_GUIDANCE` wording and re-run both eval suites until green. If a prompt change fixes Turtles but regresses another case, keep iterating — a net regression is not shippable.
- **REQ-014**: `services/openrouter_taxon_client.py` and the OpenRouter branch of `services/ai_observability.py` contain **no model-specific logic** — no `if model == ...` branching, no per-model response parsing, no model-name string checks. The model identity lives only in the single `MODEL` constant. Swapping to any other OpenRouter-hosted chat-completions model must require changing that one constant and re-running the eval suites (REQ-013) — nothing else. This is the actual deliverable of the slice; `google/gemini-3.7-flash` is just the first value.
- **REQ-015**: `services/anthropic_client.py`'s `resolve_taxon_filters` stays intact (PAT-001) **and stays test-covered** — retain at least one unit test that exercises it with a mocked Anthropic-SDK-shaped response, so the documented rollback ("swap the constant / import back to the Anthropic path") remains a verified, working path rather than untested dormant code. The `test_query_consent_wiring.py` tests being rewritten to the OpenAI shape (REQ per Verified Facts) must not leave the Anthropic function with zero coverage.

**Dependencies & config**
- **REQ-012**: `app/backend/requirements.txt` gains `openai==2.53.0`.
- **CON-001**: No runtime/env-var provider toggle. This is a build-time constant swap (the model string in `services/openrouter_taxon_client.py`), following this exact codebase's own precedent for the same call site (`spec-tool-llm-guardrails-gbif-query-040826.md` REQ-006: the earlier Sonnet→Haiku swap was also a redeploy-time constant change, not a live flag) and consistent with this project's stated default to avoid feature flags absent a concrete need for one.
- **CON-002 (security/disclosure convention, per `spec-tool-llm-guardrails-gbif-query-040826.md`'s established pattern)**: the exact `timeout=` value (REQ-010) is not mandated in this document — a build-time constant, chosen at implementation time.
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
- `resolve_taxon_filters(query, client, on_response=None, **extra_kwargs) -> list[dict]` — calls `client.chat.completions.create(model=MODEL, messages=[...], tools=[QUERY_SCHEMA_TOOL_OPENAI], tool_choice={...}, timeout=<constant>, **extra_kwargs)`, parses per §5, calls `on_response(response)` if provided (same calling convention `routers/query.py` already relies on).
- Timeout (REQ-010) — just the `timeout=` value passed to `client.chat.completions.create` (or set on the client at construction). No `ThreadPoolExecutor`/`future.result(timeout=...)` wrapper. Contract stays "return `list[dict]` or raise," matching `anthropic_client.py`.
- No model-specific code (REQ-014) — `MODEL` is the only place the model name appears.

**Modified**: `app/backend/services/ai_observability.py`
- Add the OpenRouter/OpenAI client-construction branch (REQ-006). Keep the existing Anthropic branch untouched (still used by `narration.py`'s call site and available as rollback).

**Modified**: `app/backend/routers/query.py`
- `_resolve_taxon_filters`: swap the import (`from services.openrouter_taxon_client import resolve_taxon_filters`) and the `ai_observability.build_client(...)` call to request the OpenRouter-bound client with `api_key=resolve_openrouter_api_key()` (REQ-007) instead of `resolve_api_key()` (Anthropic).
- `_capture_usage`: read `.prompt_tokens`/`.completion_tokens` instead of `.input_tokens`/`.output_tokens` (REQ-009).

**Modified**: `app/backend/requirements.txt`
- Add `openai==2.53.0` (REQ-012).

**Updated tests**: `app/backend/tests/test_query_consent_wiring.py`, relevant cases in `app/backend/tests/test_query.py` — rewrite mocks to the OpenAI-shaped response contract (§5), per REQ described in Verified Facts. Keep one test covering `anthropic_client.resolve_taxon_filters` with an Anthropic-shaped mock (REQ-015).

**Evals repointed and made to pass** (REQ-013): `app/backend/tests/evals/test_taxon_resolution_eval.py` and `app/backend/tests/evals/test_full_pipeline_eval.py` exercise the new path; iterate on `TAXON_GUIDANCE` until both are fully green (Turtles included). Prompt edits land in `services/anthropic_client.py` (the single source of truth, imported by the new module per GUD-002).

# 7. Acceptance Criteria

- **AC-001**: Given `POST /api/query` with a resolvable free-text query, when the request is handled, then the taxon filters returned come from a real OpenRouter call to `google/gemini-3.7-flash`, not Anthropic Haiku — verifiable via a live smoke test and by inspecting the PostHog event's `$ai_model`.
- **AC-002**: Given `consent=True`, when `_resolve_taxon_filters` runs, then a `$ai_generation` PostHog event is captured with `$ai_model: "google/gemini-3.7-flash"` and `$ai_base_url` containing `openrouter.ai` — verifiable via `test_ai_observability_capture_eval.py`-style live check (see §8) and manually in the PostHog dashboard (carries over the still-open item from `WORK_SUMMARY_210826.md`: no one has yet visually confirmed a narration-equivalent trace in the dashboard — this AC extends that same manual confirmation to the taxon-resolution call).
- **AC-003**: Given `consent=False`, when `_resolve_taxon_filters` runs, then no PostHog event is captured and no `posthog_distinct_id`-shaped kwarg reaches the OpenRouter call — same behavior as today's Anthropic path, verified by an updated version of `test_query_consent_wiring.py`'s existing `test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs`.
- **AC-004**: Given `test_taxon_resolution_eval.py` **and** `test_full_pipeline_eval.py` run live against the new production call path (not the old Anthropic path), then **every case passes** — all 25 taxon-resolution cases including "I want to see Turtles" → `{"taxonRank": "class", "taxonValue": "Testudines"}`, and the full pipeline eval. The Turtles gap from prototyping (§2) is closed within this slice by tightening `TAXON_GUIDANCE` wording (REQ-013); eval expectations are not relaxed. Shipping with any failing eval case is out of the question.
- **AC-005**: Given the OpenRouter call exceeds the configured `timeout=` (REQ-010), when it fires, then the request fails rather than hanging — covered by `openai`'s own timeout behavior. A unit test mocks the client raising a timeout error and asserts it propagates (no bespoke deadline mechanism to test).
- **AC-006**: Given the same queries run through the new path vs. Haiku's current behavior, then output is unchanged in shape and correctness across the entire eval set — this is a cost/provider swap with no behavior regression.
- **AC-007**: Given a hypothetical future swap to a different OpenRouter model, when the change is made, then it is confined to the single `MODEL` constant — verifiable by inspection: `grep` for the model string across `services/openrouter_taxon_client.py` and `services/ai_observability.py` finds it only at the constant's definition (REQ-014).
- **AC-008**: Given this slice is merged, when the test suite runs, then `anthropic_client.resolve_taxon_filters` still has at least one passing unit test with an Anthropic-shaped mocked response — the manual rollback path (PAT-001) stays verified (REQ-015).

# 8. Test Strategy

Full TDD per `/tdd`/`/testing` — this is production code, not prototype code.

**Unit tests** (mocked, no real API calls, red-green-refactor):
- `services/openrouter_taxon_client.py`: new test file mirroring the shape of tests already covering `anthropic_client.py`'s `resolve_taxon_filters` (if any exist at that granularity — check first) or, if none exist at that layer today, at minimum cover: (a) a well-formed tool-call response parses to the expected `taxonFilters`; (b) a response with no tool call raises/propagates rather than returning `[]` silently (REQ-005); (c) malformed JSON in the tool-call arguments raises/propagates; (d) a mocked client raising a timeout error propagates (REQ-010 — no bespoke deadline mechanism, just confirm the error isn't swallowed); (e) no model-name string appears outside the `MODEL` constant (REQ-014 — can be a simple source-scan assertion or just covered by code review).
- `app/backend/tests/test_query_consent_wiring.py`: rewrite all three existing tests (`test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs`, `test_consent_true_builds_an_observed_client_and_passes_distinct_id`, `test_returns_the_taxon_filter_and_token_usage`) to mock the OpenAI-shaped `client.chat.completions.create` return value (§5's response shape) instead of the Anthropic-shaped one currently mocked.
- `app/backend/tests/test_query.py`: any test patching `routers.query._resolve_taxon_filters` directly is unaffected (mocks the function boundary, not the client shape) — verify this holds; only tests reaching into the client-construction/response-parsing layer need updates.
- `services/ai_observability.py`: new test(s) covering the OpenRouter/OpenAI branch of `build_client` (or the new sibling function per REQ-006's implementer's-choice) — consent True/False, correct `base_url`, correct wrapper class used.
- **Rollback coverage (REQ-015)**: retain (or add, if the rewritten consent-wiring tests would otherwise remove all of it) at least one unit test that calls `anthropic_client.resolve_taxon_filters` with an Anthropic-SDK-shaped mocked response and asserts it still returns the expected `taxonFilters` — so PAT-001's manual rollback stays a tested path.

**Eval tier** (real API calls, not CI-gating — but see REQ-013: these must all pass before the production deploy):
- `app/backend/tests/evals/test_taxon_resolution_eval.py`: update its imports to call the new production entrypoint (`services.openrouter_taxon_client.resolve_taxon_filters` via `routers/query.py`'s actual construction path, or the module directly — implementer's choice, but it must exercise the real new code path, not remain pointed at `anthropic_client.py`). Run it; iterate on `TAXON_GUIDANCE` wording until all 25 cases pass, Turtles included. Seeded queries/expected values do not change.
- `app/backend/tests/evals/test_full_pipeline_eval.py`: run against the new path too — it must stay fully green after any `TAXON_GUIDANCE` edit made to fix taxon-resolution cases (guards against a prompt tweak that fixes one eval and breaks the end-to-end pipeline).
- `app/backend/tests/evals/test_ai_observability_capture_eval.py`: add or extend a case for the OpenRouter path (consent=True → real `$ai_generation` event with `$ai_model` = the new model), following the exact existing pattern (`REQUIRES_POSTHOG` skip guard, explicit `.flush()` before asserting, per ADR-009's noted delivery-confirmation requirement).

# 9. Rationale & Context

This slice exists because: (a) PRD Slice 11 explicitly scopes "LLM/AI provider abstraction... support provider/model swapping" as a planned post-MVP optimisation, and (b) this session's prototype work (`prototypes/scripts/model_comparison_spike.py`, `WORK_SUMMARY_280826.md`) found a concrete, verified case for it — `google/gemini-3.7-flash` is materially cheaper (62.5% per-token, ~3.5x on one real observed call) with only one known behavioral miss out of 25 real production eval queries, and no reliability/timeout issues in that run (unlike the other three candidates tested, which all showed real correctness or reliability problems and were rejected as the choice for this slice).

The build-as-constant-not-flag decision (CON-001) follows this codebase's own established precedent: the earlier Sonnet→Haiku swap for this identical call site (`spec-tool-llm-guardrails-gbif-query-040826.md` REQ-006) was also a redeploy-time constant change, not a live toggle — consistent with CLAUDE.md's general guidance to avoid feature flags absent a concrete need, and this project's current solo/pre-launch traffic profile doesn't yet demand live provider switching. What this slice *does* invest in is making that redeploy-time swap cheap and safe: a model-agnostic client (REQ-014) plus eval suites repointed at the real path (REQ-013) mean the next model change is "edit one constant, run the evals," not a re-architecture. That is the deliverable — `google/gemini-3.7-flash` is the current pick on cost grounds, and is expected to be revisited.

The timeout requirement (REQ-010) is deliberately modest — a plain `timeout=` value, not a bespoke mechanism. The 183s stall found in prototyping was on a rejected free model (`gemini-2.5-flash-lite`); the chosen model showed no such behavior, so engineering a wall-clock enforcer now would be solving a problem this slice doesn't have.

All existing evals passing before production (REQ-013) is a hard gate, not a target: `test_taxon_resolution_eval.py` and `test_full_pipeline_eval.py` encode the behavior the product depends on, and a provider swap that regresses any of them — including the deliberately non-standard Testudines-at-`class` case — is not a swap worth making. The prototype's 24/25 is a starting point for prompt iteration, not an acceptable ship state.

# 10. Dependencies & External Integrations

- **OpenRouter**: already an integrated third-party dependency (`services/tts.py`) — this slice adds a second call site (chat completions) against the same account/API key, no new vendor relationship.
- **`OPENROUTER_API_KEY`**: already provisioned in GCP Secret Manager and Terraform (`infra/secret_manager.tf`), no new infra work.
- **`openai` Python package**: new dependency (REQ-012), required transitively by `posthog.ai.openai.OpenAI`.
- **`posthog` Python package**: already a dependency (`posthog==6.9.3`), already includes the `posthog.ai.openai` module — no version bump needed (verified: the module is present in the currently-pinned version).

# 11. Examples & Edge Cases

- **Turtles case — must be fixed, not accepted (REQ-013, AC-004)**: "I want to see Turtles" → Gemini 3.7 Flash returned `taxonRank: "order"` instead of this app's deliberately non-standard `taxonRank: "class"` for Testudines (§2). This was the only miss in the 25-case seeded eval — every other query matched exactly, including all negation cases, the 7-entry fish expansion, the 4-class reptile expansion, and multi-taxa ordering. The fix is a `TAXON_GUIDANCE` wording change (make the "GBIF has no Reptilia class, so query Testudines/Squamata/etc. at `class` rank" instruction unambiguous enough that the new model follows it), verified by re-running both eval suites. Not a shippable known regression.
- **Per-read timeout quirk (REQ-010)**: seen live on a *rejected* candidate model (`google/gemini-2.5-flash-lite`, not the chosen one) during prototyping — `httpx`'s timeout bounds per-read gaps, not total time. `google/gemini-3.7-flash` showed no such behavior across the full eval run. This slice's response is a plain `timeout=` constant, nothing more; a bespoke wall-clock enforcer is explicitly out of scope (§1). Note it here only so it isn't re-introduced as a "safeguard" during implementation.
- **Dict key-order variance**: OpenRouter/Gemini responses were observed returning `{"taxonValue": ..., "taxonRank": ...}` as often as the reverse key order — confirmed harmless (§5), noted here only so it isn't mistaken for a bug during implementation review.

# 12. Validation Criteria

- All unit tests (§8) pass, including the rewritten Anthropic→OpenAI-shaped mocks and the retained Anthropic-shaped rollback test (REQ-015).
- `test_taxon_resolution_eval.py` **and** `test_full_pipeline_eval.py` run live against the new production path — **all cases pass**, Turtles included, with `TAXON_GUIDANCE` edited as needed and eval expectations unchanged (REQ-013). No production deploy while any eval case fails.
- Model identity confined to the single `MODEL` constant (AC-007) — confirmed by `grep`.
- A live smoke test against a local/staging `POST /api/query` call, confirming a real OpenRouter round-trip and a correct `taxonFilters` response — per this project's general convention (CLAUDE.md) of validating production coding work end-to-end, not just via unit tests.
- Manual confirmation in the PostHog dashboard of a real `$ai_generation` event for this call site with `$ai_model: "google/gemini-3.7-flash"` (AC-002) — extends the still-open PostHog-dashboard-confirmation item already carried over from `WORK_SUMMARY_210826.md` for narration.
- `ruff`/`mypy` clean (per this project's established pre-push habit, `WORK_SUMMARY_210826.md`'s CI-failure lesson: run both locally before pushing, since CI runs both and local `.env` state can mask a real local/CI environment gap).

# 13. Related Specs / Further Reading

- `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md` — the original taxon-resolution slice this one modifies; §2/§4 REQ-006-008 describe the call site and secret-handling pattern this spec follows.
- `docs/decisions/ADR-009-posthog-ai-observability-wrapper-client.md` — directly predicted and validated the `posthog.ai.openai.OpenAI(base_url=...)` migration path this spec implements.
- `docs/decisions/ADR-006-secrets-environments-region-data-handling.md` — the explicit-Secret-Manager-fetch pattern `OPENROUTER_API_KEY` already follows.
- `docs/prds/nature-quest-prd-300726.md` — Slice 11 (LLM/AI provider abstraction), the parent PRD item this spec is the first increment of.
- `docs/status_docs/WORK_SUMMARY_280826.md` — this session's prototype work, model comparison results, and the per-read timeout quirk noted in §2.
- `docs/status_docs/WORK_SUMMARY_210826.md` — narration slice; source of the still-open "no one has visually confirmed a PostHog trace" item this spec's AC-002 extends.
- `prototypes/scripts/model_comparison_spike.py`, `prototypes/scripts/server_model_comparison.py`, `prototypes/web/index_model_comparison.html` — the prototype this spec's REQ-001-005 and REQ-010 are directly derived from.
