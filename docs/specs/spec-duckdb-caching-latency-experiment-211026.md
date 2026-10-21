# Experimental Brief: Isolating and Fixing `/api/query`'s Per-Request Setup Cost

## Status
Draft — investigation plus a recommendation. The new latency instrumentation is a permanent addition, not throwaway diagnostic code.

## Branch
`explore_duckdb_caching`, based off `main` (not off `monthly_gbif_data_update`, which holds unrelated in-progress work to return to separately).

## Background

Live production testing (5 sequential `curl` requests against `/api/query`) measured 3.4–4.5s time-to-first-byte — DNS/TLS/connect overhead ruled out (~0.2–0.3s, confirmed via `curl`'s own timing breakdown). Pulling structured Cloud Logging timestamps (`query_submitted` → `llm_taxon_filters_resolved` → `species_selected` → `species_enriched`) split this into three coarse stages, consistent across all 5 runs with no visible warm-up trend:

| Stage | Observed range |
|---|---|
| LLM taxon resolution | 1.7–3.0s |
| DuckDB query + GCS auth setup | 1.3–1.7s |
| Wikipedia enrichment | 0.19–0.26s |

The no-warm-up-trend observation is consistent with (but doesn't yet prove in isolation) the hypothesis that **nothing is cached or reused across requests today**. Looking across the whole pipeline, not just the DuckDB piece, turned up a pattern repeating in at least four places — each rebuilt from scratch on every single request, none of them previously measured in isolation:

1. **`species_index.open_index_connection()`** (DuckDB phase): fresh `duckdb.connect()`, `INSTALL`/`LOAD httpfs`, two separate Secret Manager API calls (each involving `google.auth.default()`'s own metadata-server round-trip plus constructing a fresh gRPC `SecretManagerServiceClient`), then `CREATE SECRET` — all repeated on every request.
2. **`routers.query._resolve_taxon_filters()`** (LLM phase): `tts.resolve_api_key()` does its own fresh Secret Manager fetch for the OpenRouter key, and `ai_observability.build_openrouter_client()` constructs a brand-new SDK client object (and therefore a fresh underlying HTTP connection pool) — both per request.
3. **`ai_observability.build_openrouter_client()`'s consent branch**: when `consent=True`, this returns `posthog.ai.openai.OpenAI`, a wrapper that captures full `$ai_input`/`$ai_output_choices` around every call. That wrapper's own construction and its per-call capture overhead is a separate cost on top of (2) — and every latency number measured so far ran with whatever consent state happened to be set at the time, untracked as a variable. Needs to be controlled for, not ignored.
4. **`wikipedia_client._fetch_summary()`**: calls `httpx.get()` directly with no persistent client at all — a smaller cost (no Secret Manager involvement), but still a fresh connection per call, and there are up to 5 of these per request (one per selected species).

Given four independent "rebuilt fresh every request" costs, not one, this isn't really a narrow "DuckDB connection caching" question any more — it's a pipeline-wide pattern, and worth treating as one.

Two further open questions surfaced in discussion, not yet tested:

- **Does measurement method matter?** All production numbers so far came from `curl` in a loop, which opens a fresh TCP+TLS connection on every call — a real difference from a browser, which reuses one persistent connection. A subjective "feels faster after the first load" impression from real browser use has not been timed or confirmed.
- **How much of the DuckDB-phase cost is GCS/Secret-Manager-specific versus a generic "first query against a Parquet file" cost?** Locally, `open_index_connection()` skips all GCS/Secret-Manager logic entirely (`K_SERVICE` unset) and reads local files directly — so local measurements, by construction, isolate the LLM/Wikipedia/PostHog cost from everything GCS-related, without needing new code to prove it.

## Objective

Two things, not one:

1. Get real, measured numbers for where `/api/query`'s latency actually comes from, per stage and per sub-step, across both environment (local vs. production) and access method (curl vs. browser).
2. Based on those findings, **assess and recommend the full set of per-request setup work that's worth moving off the request path entirely** — not just a binary choice between DuckDB's Option A and Option B, but across all four costs identified above (DuckDB+GCS auth, OpenRouter client+key, the PostHog wrapper, Wikipedia's per-call connections), including how far a startup-time warming approach could go in removing the cold-start cost for each of them. The number of independent "fresh connection per request" patterns found already suggests there may be a cluster of genuinely quick wins here, not just one.

## Design: a 2×2 matrix

|              | **curl** | **browser** |
|---|---|---|
| **Local** | 1 | 2 |
| **Production** | 3 | 4 |

- **Local vs. production, same access method** isolates GCS/Secret-Manager-specific cost — local should show ~zero DuckDB-phase cost by construction (no `httpfs`, no Secret Manager calls at all locally), leaving whatever's left as the baseline LLM+Wikipedia+PostHog+general app-overhead cost that exists regardless of this migration.
- **curl vs. browser, same environment** isolates connection-reuse/measurement-method artifacts — applied to the DuckDB/GCS phase (production only, since local doesn't touch it) and the LLM/OpenRouter/Wikipedia phases (present in both environments).

Each cell: 5 sequential requests, same fixed input each time (`query: "birds"`, the Retiro polygon), in one continuous session per cell so each method's natural connection-handling behavior (or lack of it) plays out as it would for a real user. Run once with `consent=False` and once with `consent=True` per cell, to control for the PostHog-wrapper variable rather than leave it untracked as before.

## Instrumentation — a permanent addition to observability, not a throwaway diagnostic

Query-latency visibility is useful on an ongoing basis, not just for this investigation — so this instrumentation stays, following the same structured-logging pattern already in `logging_client.py` (the existing stage-boundary log lines aren't temporary either). Add `time.perf_counter()` checkpoints inside `open_index_connection()`, `query_species_for_filter()`, `_resolve_taxon_filters()`, and `species_enrichment.enrich_species()`:

- `t_connect` — `duckdb.connect()`
- `t_install_load_httpfs` — `INSTALL`/`LOAD httpfs`
- `t_fetch_secret_gcs_key_id` / `t_fetch_secret_gcs_secret` — the two GCS-credential Secret Manager calls
- `t_create_secret` — the local `CREATE SECRET`
- `t_query_execute` — the actual DuckDB query, per taxon filter
- `t_fetch_secret_openrouter` — the OpenRouter-key Secret Manager call (`tts.resolve_api_key()`)
- `t_build_openrouter_client` — `ai_observability.build_openrouter_client()`, tagged with the consent state used
- `t_llm_call` — the actual `resolve_taxon_filters()` network call
- `t_wikipedia_fetch` — per-species Wikipedia call time, and how much the 3-way concurrency cap actually parallelizes in practice

Locally, the GCS-specific checkpoints should log as ~0/skipped entirely (confirming, not just assuming, that `open_index_connection()`'s `K_SERVICE` branch is correctly bypassed) — a useful built-in sanity check.

## Procedure

For each of the 4 cells, at both consent states:

1. **Local (cells 1–2):** start a fresh local dev server process (`uvicorn main:app --reload` cold-started fresh, not already warm from prior testing) before the first request, so the "first request" condition is comparable in spirit to a cold Cloud Run instance. Cell 1 drives it via `curl` directly against `localhost:8000`; cell 2 drives it via `agent-browser` against `localhost:5173` (the real UI, real submit flow).
2. **Production (cells 3–4):** start from a cold instance where possible (force via a trivial new revision deploy, or confirm via `gcloud run services describe`/instance metrics that it's currently scaled to zero before starting) so request 1 in each cell is a genuine cold start, not an accidental warm one. Cell 3 via `curl` against the live URL; cell 4 via `agent-browser` driving the real production UI, using its network-timing capture (`agent-browser network har start`/`network requests`) rather than relying on subjective impression.
3. Collect, for every request in every cell: total wall-clock time (via `curl -w` for cells 1/3, via the browser's own request timing for cells 2/4), plus every structured-log checkpoint from above (production: `gcloud logging read`; local: direct stdout capture from the dev server process, since there's no Cloud Logging locally).
4. Build one combined table: 4 cells × 2 consent states × 5 requests × all checkpoints.

## Analysis

- **Local vs. production (same method)**: quantifies how much of the DuckDB-phase cost is genuinely GCS/Secret-Manager-specific, versus something that would exist for any Parquet query over a network path.
- **curl vs. browser (same environment)**: tells us whether connection reuse is real and material — separately per phase — and whether the subjective "feels faster after first load" impression holds up under real timing.
- **`consent=False` vs. `consent=True`**: quantifies the PostHog-wrapper's own overhead, previously untracked.
- **Request 1 vs. requests 2–5 within each cell**: confirms or corrects the "nothing is cached today" assumption directly, per checkpoint, rather than inferring it from the absence of a trend in coarse totals.

## Recommendation framework

Rather than a single A-vs-B decision, use the measured per-checkpoint costs to independently assess each of the four candidates found, and recommend a combined set of changes — likely including some form of startup-time warming across all of them, not just DuckDB:

- **DuckDB + GCS auth**: credential caching alone (Option A) vs. full connection/cursor sharing (Option B) — per the earlier discussion, decided by how much of the phase is `t_fetch_secret_gcs_*` versus `t_query_execute`.
- **OpenRouter client + key**: same shape of fix — cache the fetched key, reuse a long-lived client object — if `t_fetch_secret_openrouter`/`t_build_openrouter_client` show a comparable pattern to the DuckDB side.
- **PostHog wrapper**: if its overhead is material, assess whether it can be constructed once per process (like the plain client) rather than per request, independent of whether `consent` varies per request.
- **Wikipedia client**: assess whether a shared `httpx.Client()` (connection pooling across the up-to-5 per-request calls, and across requests) is worth adding, given it's the smallest of the four but also the simplest to fix.
- **Startup-time warming**: for whichever of the above show a real cold-start-specific cost (not just a per-request-repetition cost), assess moving that setup into a FastAPI startup hook (`main.py` has none today) so it happens once when an instance boots, before Cloud Run routes any real traffic to it — trading slower instance-ready time for zero user-visible cold-start cost. Recommend this per-candidate based on measured cold (request 1) vs. warm (requests 2–5) deltas, not uniformly.
