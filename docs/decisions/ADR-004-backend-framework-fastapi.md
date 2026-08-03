# ADR-004: Backend framework — FastAPI over Flask

## Status
Accepted

## Date
2026-07-30

## Context
All prototype backends (`prototypes/scripts/server*.py`) use Flask with synchronous `requests` calls and `concurrent.futures.ThreadPoolExecutor` for parallel GBIF fetches. Per this project's convention, nothing in `prototypes/` is imported into production code, so the production backend is a fresh build regardless of which framework is chosen. The real pipeline's dominant cost is IO-bound waiting (GBIF, Wikipedia, Anthropic API calls), and its LLM calls already produce structured output against defined schemas (e.g. `QUERY_SCHEMA_TOOL`) that are currently validated only informally.

## Decision
Build the production backend with **FastAPI**, not Flask.

## Alternatives Considered

### Flask
- Pros: matches everything used in prototyping, zero new learning curve, huge ecosystem, simple mental model.
- Cons: WSGI-based — genuine concurrency requires multiple worker processes/threads (coarser-grained, more memory per Cloud Run instance) rather than true event-loop concurrency; no native request/response validation (would need manual Pydantic wiring or a third-party extension like `Flask-Pydantic`/`apiflask` to get equivalent behavior to FastAPI's built-in story); a long-held streaming response (for future per-step progress updates) ties up a full sync worker for its duration.
- Rejected because: the pipeline is IO-bound and benefits materially from ASGI's event-loop concurrency, and this project already chose TypeScript/React (ADR-003) specifically for type safety and tooling — FastAPI is the backend counterpart to that same reasoning, particularly via its native Pydantic integration (see Consequences).

## Consequences
- **Real porting cost, stated plainly**: FastAPI's async advantage only holds if the codebase follows async best practices consistently (async-compatible HTTP clients, no unwrapped blocking calls in request handlers). This requires deliberate engineering discipline throughout the port from the prototypes' synchronous code, not an afterthought — see internal implementation notes for specifics.
- Deployment requires an ASGI server (Uvicorn, typically run under Gunicorn as a process manager) rather than Flask's simpler default WSGI/Gunicorn pattern — one more moving part in the Dockerfile, though a standard, well-documented one.
- Pydantic models become the single definition point for: the LLM tool-use schema fed to the Anthropic API, runtime validation of LLM output (a real guardrail against the hallucination risk already flagged in `docs/status_docs/PLANNING_INTENT_QUERY_210726.md` for taxon ranks/values), the FastAPI endpoint contract, auto-generated OpenAPI docs, and generated TypeScript types for the React frontend (ADR-003) — five concerns kept in sync from one model definition instead of five places that could drift.
- Pydantic `ValidationError`s can be registered as a distinct, loggable/alertable error type — directly useful for the monitoring goal of surfacing LLM structured-output failures as a first-class health signal (see ADR-006/monitoring scope), rather than indistinguishable generic 500s.
- Sets up a clean interface boundary for the future LLM/AI provider abstraction slice (PRD Slice 11) and the future evaluation harness slice (PRD Slice 12) — both become easier when every pipeline stage's input/output is already a validated Pydantic model.
- FastAPI natively supports Server-Sent Events/streaming responses, which maps directly onto the "background-job + polling for real per-step progress" item flagged as open in `prototypes/README.md` — Flask can do this too, but less naturally under its sync-worker model.
