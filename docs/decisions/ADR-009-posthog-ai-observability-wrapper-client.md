# ADR-009: PostHog AI Observability via wrapper client, not OpenTelemetry instrumentation

## Status
Accepted — supersedes the OTel-based design in `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`'s REQ-019/§6/§10 as originally written.

## Date
2026-08-06

## Context
REQ-019 (server-side capture of every real Anthropic call — input, output, tokens, cost, latency — as PostHog `$ai_generation` events) was originally specified as OpenTelemetry-based: `AnthropicInstrumentor().instrument()` patching the Anthropic SDK process-wide, plus a `posthog.ai.otel.PostHogSpanProcessor` attached to an OTel `TracerProvider`.

Building against that design surfaced a real mismatch with REQ-020 (the frontend's PostHog `distinct_id` must be passed per request, so server-side and client-side events attribute to the same visitor). The OTel `TracerProvider` is constructed once, at process startup, and `distinct_id` is set on it as a `Resource` attribute — a process-level value, not something that can vary per incoming HTTP request. Reading the actual `posthog-python` GitHub examples (`examples/example-ai-anthropic/chat.py`) confirmed this is how the library's own reference implementation is built: one `distinct_id`, baked in at `Resource` construction, for the life of the process.

## Decision
Use `posthog.ai.anthropic.Anthropic` instead — a wrapper subclass of the real `anthropic.Anthropic` client (`posthog/ai/anthropic/anthropic.py` in the installed package). Same `.messages.create()` call shape, but accepts `posthog_distinct_id`/`posthog_trace_id`/`posthog_privacy_mode` as **per-call** keyword arguments, and captures `$ai_input`/`$ai_output_choices` (full sanitized request/response), model, tokens, latency, and HTTP status on every call — verified by reading the wrapper's source directly, not assumed from docs.

`services/ai_observability.py` implements the actual consent-gating: `build_client(consent, distinct_id, api_key)` returns the plain `anthropic.Anthropic` when `consent=False`, or the PostHog-wrapped client when `consent=True`, lazily constructing/reusing a singleton `PostHogClient` from `POSTHOG_PROJECT_TOKEN`/`POSTHOG_HOST` env vars.

## Alternatives Considered

### OpenTelemetry (`AnthropicInstrumentor` + `PostHogSpanProcessor`), as originally spec'd
- Pros: standard OTel tracing, generalizes to non-Anthropic instrumentors already published for other providers.
- Cons: `distinct_id` is process/tracer-level, not per-request — the actual blocker. Also more infrastructure (`TracerProvider`, `Resource`, span processor wiring) for no benefit at this project's current scale.
- Rejected because: incompatible with REQ-020 as specified, without a workaround (e.g. a new `TracerProvider` per request) that would itself be more complex than the wrapper-client alternative.

### DeepEval's `@observe`/tracing, considered during the same build session for local eval-run visibility
- Pros: pytest-native, would in principle unify eval-suite tracing with production observability.
- Cons: verified empirically that DeepEval's local tracing produces no visible span input/output at all without a paid Confident AI account — the "runs entirely locally" language in its docs refers to computation, not console visibility. A real library bug was also hit (`ToolSpan` crashes when `name=` is passed to `@observe(type="tool", ...)`, since `Observer` reads `name` out of `observe_kwargs` without removing it, then spreads `observe_kwargs` again for `ToolSpan`).
- Rejected because: contributed no functional value once verified — the `deepeval` dependency and all `@observe`/`update_current_span` calls were removed from `tests/evals/`; real eval-run visibility comes from plain `print()` statements instead, and real production-parity observability from this ADR's PostHog wrapper client, which needs no separate tracing layer to be readable.

## Consequences
- `docs/specs/spec-tool-llm-guardrails-gbif-query-040826.md`'s REQ-019 (implementation), §6 (`services/ai_observability.py`'s described shape), and §10 (dependency list: `opentelemetry-instrumentation-anthropic`/`opentelemetry-sdk` should read `posthog`; `posthog[otel]` should read `posthog`) are stale relative to the actual build and should be read alongside this ADR, not as the literal implementation.
- REQ-024's eval suite no longer depends on `deepeval` — its "built on DeepEval" framing is likewise superseded.
- Migrating to a different LLM provider later (e.g. via OpenRouter, PRD Slice 11's deferred general provider abstraction) stays low-friction: `posthog.ai.openai.OpenAI` follows the identical pattern (subclasses `openai.OpenAI`, passes `**kwargs` through, so `base_url="https://openrouter.ai/api/v1"` works directly) — confirmed by reading its source, not assumed.
- `posthog`'s Python client batches events via an internal queue + background thread; `capture()` returns before the event is actually sent. Anything that needs delivery confirmed before a short-lived process exits (e.g. the eval suite's optional PostHog-capture check, `tests/evals/test_ai_observability_capture_eval.py`) must call `.flush()` explicitly.
