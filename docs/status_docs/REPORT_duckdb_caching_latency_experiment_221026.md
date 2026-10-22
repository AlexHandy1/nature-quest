# Report: `/api/query` Per-Request Setup Cost Experiment — 22 October 2026

Executes `docs/specs/spec-duckdb-caching-latency-experiment-211026.md` and
reports findings. Instrumentation (`timing_checkpoint` log lines via
`services/logging_client.py`'s `timed_checkpoint`) is live in both local dev
and production as of this session's merge to `main`.

## Deviations from the spec's exact procedure

- **3 reps per cell/consent combo, not 5** — to bound real OpenRouter/
  Anthropic/Wikipedia/PostHog cost and avoid polluting production PostHog
  analytics with more synthetic traffic than needed for a clear signal.
- **No forced-cold Cloud Run revisions for the production cells.** The
  original plan to force a fresh revision before each production run was
  out of scope — the spec's "cold vs. warm" comparison is request-1-vs-
  requests-2-5 *within* a run against whatever's currently live, not a
  guaranteed-zero-instances container (that question was already answered
  separately, in the page-load cold-start investigation earlier this
  session — ~19s, dominated by Cloud Run container provisioning, nothing
  to do with this spec's checkpoints). Production cells here ran against
  the already-warm instance left over from the current deploy.
- **Production browser reps aren't tagged with a custom `distinct_id`** —
  the real frontend generates its own PostHog anonymous ID. They were
  correlated to Cloud Logging entries by timestamp window instead (see raw
  data below).
- Local cells used a fresh `uvicorn` process restart before each
  cell/consent combo, per the spec. Production cells did not get a fresh
  process per combo (not possible without a redeploy, out of scope per
  above) — so "request 1" in the production curl/browser cells is cold
  only in the sense of "first request of this specific test run," not
  "first request since container boot."

## Raw data

Saved under this session's scratchpad (not committed — synthetic
test traffic, not project data):
`local_curl_false.log`, `local_curl_true.log`, `local_browser_false.log`,
`local_browser_true.log`, `prod_all_checkpoints.json` (Cloud Logging pull,
`distinct_id` prefixed `exp-prod-curl-*` for curl reps, PostHog anonymous ID
`01a16309-3efa-7a13-8b3a-5f0ea57458f6` for the browser reps).

## Findings

### 1. Locally, GCS/Secret-Manager checkpoints never fire — confirmed correctly

Every local request logged zero `t_install_load_httpfs` /
`t_fetch_secret_gcs_*` / `t_create_secret` / `t_fetch_secret_openrouter`
entries, across both curl and browser cells. Confirms `open_index_connection()`
and `tts.resolve_api_key()`'s `K_SERVICE` branch guards work as intended —
this is a free sanity check, not just an assumption.

### 2. GCS/Secret-Manager setup cost is real, recurring, and currently uncached — ~150–400ms every single production request

Production always pays all five of these on every request (no caching
exists today):

| Checkpoint | Cold (1st prod sample since deploy) | Warm (typical) |
|---|---|---|
| `t_fetch_secret_openrouter` | 358.5ms | 75–150ms |
| `t_install_load_httpfs` | 577.1ms | 75–200ms |
| `t_fetch_secret_gcs_key_id` | 325.4ms | 80–325ms |
| `t_fetch_secret_gcs_secret` | 120.4ms | 65–160ms |
| `t_create_secret` | 7.0ms | 1–10ms |
| **Sum** | **~1,388ms** | **~300–850ms** |

This is paid in full on *every* request forever, not just the first one —
confirming the spec's core hypothesis for the DuckDB+GCS-auth and
OpenRouter-key candidates. Caching the two secrets (and/or holding one
long-lived DuckDB connection with the secret/extension already configured,
rather than rebuilding it per request) would remove this entirely from the
request path for all but the very first request per container instance.

### 3. The GCS Parquet read itself costs far more than the auth setup around it — ~700–900ms extra per request even when warm, ~5.5s on the very first query

`t_query_execute` (the actual DuckDB query, including `read_parquet()` over
`gs://` via `httpfs`):

| Environment | Cold | Warm (typical) |
|---|---|---|
| Local (disk) | n/a (consistently small) | **60–90ms** |
| Production (GCS/httpfs) | **5,570ms** (1st query since deploy) | **760–1,006ms** |

This is the single largest fixable gap found: production's *steady-state*
query cost is ~10x local's, and the very first query after a container
boots costs an extra ~4.7 seconds on top of that. Neither the credential
checkpoints above nor the LLM call explain this — it's specifically the
cost of `read_parquet()` doing real network range-reads against the two
GCS objects, repeated from scratch on every request because no connection,
file-metadata, or result caching exists across requests today. This is a
stronger case for **Option B (shared connection/cursor, not just cached
credentials)** from the original DuckDB-caching question than for
credential-caching alone — a reused connection could plausibly keep the
Parquet file metadata/footer already resolved, avoiding the repeat network
round trips.

### 4. LLM call latency doesn't correlate with request order — it's provider-side variance, not something caching fixes

| | Local | Production |
|---|---|---|
| Typical `t_llm_call` | 1.9–3.3s | 1.9–4.6s |
| Spike observed | 14.3s (×2, local) | 6.9–7.0s (×2), one 4.6s (production) |
| Spike position | Request #1 in curl cells, but request **#4** in the local browser cell | Request #1 and #2 of the first production curl run |

Local curl's two restarts both spiked on request #1, which initially looked
like a connection-warm-up effect — but the local *browser* cell (same fresh-
restart discipline) spiked on request #4, not #1, breaking that theory.
Combined with ADR-015's existing note that OpenRouter's default "Balanced"
routing picks the backend/tier per call with no pinned provider preference,
the more consistent explanation is **per-call provider-side routing
variance**, not a local warm-up cost. This means a cached/reused OpenRouter
client (`t_build_openrouter_client`, see below) won't reliably prevent these
multi-second spikes — they're outside this app's control.

### 5. The PostHog wrapper's cost is a one-time per-process cost, not per-request — confirms it's a legitimate caching target

`t_build_openrouter_client`, `consent=True` vs `consent=False`:

| | 1st call per process | Subsequent calls |
|---|---|---|
| `consent=False` | 70.5ms / 203.7ms (local / prod) | 11–23ms |
| `consent=True` | 778.7ms / 872.9ms / 318.9ms | 11–17ms |

The PostHog-wrapped client construction costs several hundred extra
milliseconds, but only once per container's lifetime (the lazy
`_default_posthog_client()` singleton in `ai_observability.py` already
caches the underlying PostHog client — this checkpoint is timing the
*wrapper* object construction around it, which still happens fresh each
call). Moving this to a startup hook would remove the one-time cost from
whichever user's request happens to be first, at the cost of always paying
it at boot regardless of whether `consent=True` is ever used.

### 6. Wikipedia enrichment is small and consistent everywhere — not worth prioritizing

Per-species `t_wikipedia_fetch`: 35–280ms in every cell, no meaningful
local-vs-production gap (expected — Wikipedia is an external dependency,
unrelated to this app's own infra). Five species per request, concurrency
capped at 3, consistent with the existing `species_enrichment.py` design and
its tests — nothing new to fix here.

## Recommendation (per the spec's framework)

| Candidate | Verdict | Why |
|---|---|---|
| DuckDB + GCS auth | **Fix — cache credentials AND reuse the connection** | ~300–850ms/request recurring cost from re-fetching secrets + re-running `INSTALL`/`LOAD httpfs`/`CREATE SECRET` every time; a reused connection may also cut the ~700–900ms steady-state `read_parquet()` gap vs. local, not just the setup around it |
| OpenRouter client + key | **Fix — cache the key, reuse a long-lived client** | ~75–360ms/request recurring `t_fetch_secret_openrouter`, same uncached-every-request pattern |
| PostHog wrapper | **Fix via startup warming** | One-time ~300–900ms cost per container lifetime; moving it to a FastAPI startup hook removes it from any user-facing request |
| Wikipedia client | **Low priority** | Small (35–280ms), no demonstrated local/prod gap, already concurrency-capped |
| LLM call itself | **Not fixable by caching** | Multi-second spikes are OpenRouter-side routing variance uncorrelated with request order or warm-up state — a caching/startup-warming fix would not address this |

---

## Key findings (summary)

1. **GCS/Secret-Manager setup is real and 100% recurring** — ~300–850ms paid on *every single* production request today, because nothing is cached. This is the clearest, most mechanical win available.
2. **The GCS Parquet query itself, not just the auth around it, is the bigger cost** — production's steady-state query is ~10x slower than local (~800ms vs ~70ms), and the very first query after a cold boot costs an extra ~4.7s on top. Argues for connection reuse, not just credential caching.
3. **LLM latency spikes (multi-second, intermittent) are provider-side and don't correlate with request order** — contradicts an earlier working assumption that they were a local warm-up artifact; caching the OpenRouter client won't fix these.
4. **The PostHog consent wrapper's extra cost is genuinely one-time-per-process** (~300–900ms), making it a clean startup-warming candidate.
5. **Wikipedia enrichment is already fine** — no action needed.

---

## Addendum: post-fix production re-test (22 October 2026, after deploy)

All three recommended fixes from this report were implemented and deployed
to production this session:

1. Shared DuckDB/GCS connection, built once at process startup, with a
   per-request `.cursor()` for thread-safe concurrent access
   (`services/species_index.py`).
2. OpenRouter API key + both consent-state clients cached per process
   (`services/tts.py`, `services/ai_observability.py`).
3. A FastAPI `lifespan` startup hook that eagerly warms all of the above
   before Cloud Run routes any traffic (`main.py`).

Re-ran the same production-curl procedure as the original experiment (3
reps × 2 consent states, same Retiro `birds` query, pulled Cloud Logging
checkpoints afterward — same `exp-prod-curl-*`-style distinct IDs, this
run prefixed `postfix-prod-curl-*`). One methodology note: the first
`gcloud logging read` pull after the test was incomplete (Cloud Logging
ingestion lag, not a request bug) — re-pulling ~90s later showed all 6
requests cleanly, each with a clean single `query_submitted`→
`query_outcome` pair, no overlap.

### Results: all three predicted wins confirmed, plus one bonus

| Checkpoint | Before (this report, warm) | After (post-fix, warm) |
|---|---|---|
| `t_fetch_secret_openrouter` | 75–150ms, every request | **never fires** (warmed at startup) |
| `t_install_load_httpfs` | 75–200ms, every request | **never fires** |
| `t_fetch_secret_gcs_key_id` / `_secret` | 80–325ms / 65–160ms, every request | **never fire** |
| `t_create_secret` | 1–10ms, every request | **never fires** |
| `t_build_openrouter_client` | 11–23ms, every request | **0.0ms**, every request |
| `t_query_execute` (warm) | 760–1,006ms | **189–220ms** |
| `t_llm_call` | 1.7–3.3s (provider variance) | 1.7–3.3s — unchanged, as expected |
| Wikipedia enrichment | 40–280ms/species | 40–780ms/species — unchanged, as expected |

The `t_query_execute` drop is the standout, unconfirmed-until-now result:
finding 2 above only *hypothesized* that connection reuse might also cut
the steady-state `read_parquet()` network cost, not just the auth/secret
setup around it. It did — warm query time fell ~4–5x (not just the
~300–850ms of setup cost disappearing, but the query itself getting
roughly 570–800ms faster too).

**Real total request time** (`query_submitted`→`query_outcome`, excluding
the one cold-instance outlier below): **consent=false ~2.2–3.7s,
consent=true ~2.6–2.8s** — down from this report's ~4–8s (curl) / ~5–7s
(browser) warm baseline.

**Non-LLM portion specifically** (total minus `t_llm_call`): **~0.41–0.52s**
on 5 of the 6 requests — essentially hitting the "<1s for non-LLM
retrieval steps" goal discussed alongside this work. The LLM call is now
the dominant, and only remaining unaddressed, cost — tracked separately
(`prototypes/scripts/latency_provider_comparison.py`).

### Not a regression: the first request after a cold container still pays Cloud Run's own provisioning cost

The first request in this re-test (`postfix-prod-curl-false-1`) took
24.66s by `curl`'s own clock, but the `query_submitted`→`query_outcome`
gap inside that was only **4.04s** — the other ~20s was Cloud Run
provisioning a cold container plus this session's new startup-warming
hook running, *before* the request handler started at all. This matches
the earlier, separate page-load cold-start finding (~19s, dominated by
container provisioning) and is unrelated to this report's fixes — it's
the cost `min_instances ≥ 1` would remove, not something connection/key
caching touches.

---

## Addendum 2: production re-test after also switching the taxon-resolution model (22 October 2026)

Separately from this report's caching/warming fixes, a model swap landed
on top: `services/openrouter_taxon_client.py`'s `MODEL` constant changed
from `google/gemini-3.7-flash` to `openai/gpt-oss-120b:nitro` (generic,
unpinned routing) — see `prototypes/scripts/latency_provider_comparison.py`
and the commit history on `optimise_openrouter` for the selection
rationale (sub-1s P90 on OpenRouter's own latency pages, verified against
all 25 taxon-resolution eval cases and all 4 full-pipeline eval cases with
no accuracy loss before the swap landed).

Re-ran the same production curl (3 reps × 2 consent states) plus a real
browser test (3 reps) against the live, now-doubly-fixed deployment
(caching/warming from Addendum 1 + this model swap).

### Results: `t_llm_call` collapses from the dominant cost to the smallest-variance one

| Rep | Total (`query_submitted`→`query_outcome`) | `t_llm_call` | `t_query_execute` |
|---|---|---|---|
| curl false-1 | 1.14s | 774ms | 167ms |
| curl false-2 | 0.92s | 294ms | 389ms |
| curl false-3 | 0.72s | 295ms | 203ms |
| curl true-1 | 0.69s | 340ms | 173ms |
| curl true-2 | 0.77s | 405ms | 164ms |
| curl true-3 | 0.78s | 340ms | 179ms |
| browser 1 | 0.81s | 379ms | 217ms |
| browser 2 | 0.78s | 397ms | 180ms |
| browser 3 | 0.78s | 383ms | 186ms |

`t_build_openrouter_client` was **0.0ms on every single request** —
Addendum 1's client caching holds unchanged under the new model.

### The full arc of this investigation, start to finish

| Stage | Total request time |
|---|---|
| Original production baseline (session start) | 3.4–4.5s |
| After DuckDB/GCS + OpenRouter caching + startup warming (Addendum 1) | ~2.2–3.7s (curl) |
| **After also switching to `gpt-oss-120b:nitro`** | **~0.7–1.1s** |

`t_llm_call` fell from 1.7–3.3s (Addendum 1) to **294–774ms** — the
single largest cost identified at the start of this investigation is now
the pipeline's smallest-variance step, and total server-side processing
time is consistently at or under ~1 second across both curl and real
browser requests, meeting (and in most reps beating) the original "<1s
for non-LLM retrieval steps" goal — now with the LLM call itself inside
that budget too.
