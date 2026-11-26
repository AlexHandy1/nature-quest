# Work Summary — 26 November 2026

Branch: `build_wiki_embeddings_table`. Implemented steps 1-7 of the
Wikipedia-embeddings pipeline plan (`PLANNING_EMBEDDINGS_FEATURES_291026.md`
Track A / `WORK_SUMMARY_201126.md` next steps), TDD throughout, and ran the
real fetch step (step 6) to completion against the full production manifest.

## What was built

- `app/backend/jobs/wikipedia_extracts_fetch.py` + `tests/test_wikipedia_extracts_fetch.py`
  (step 1) — batched MediaWiki Action API fetch (20 titles/request,
  `exintro=1&explaintext=1&exlimit=max&redirects=1`, no length cap, full
  extract stored unmodified). `requests` + `ThreadPoolExecutor` (not the
  prototype's `asyncio`/`httpx`), paced by a thread-safe `RateLimiter` at
  200 req/min, concurrency 3. Checkpointing: every batch's rows are
  appended to a JSONL output file and flushed immediately; `run()` reads
  existing output via `read_fetched_specieskeys()` and only re-fetches
  specieskeys missing from it — the recovery mechanism this session relied
  on after a real crash (see Decisions). Added mid-session: per-batch
  progress logging (JSON log line + flushed `print`) and a
  `check_output_consistency()` step that re-reads the actual output file
  from disk at the end and compares its row count / distinct specieskey
  count against the manifest total, rather than trusting in-memory
  counters.
- `app/backend/jobs/wikipedia_embed_extracts.py` + `tests/test_wikipedia_embed_extracts.py`
  (step 2) — reads the fetch step's *finished* JSONL in one pass (no
  per-batch interleaving with step 1), embeds species with a successful
  extract via fastembed/MiniLM (`sentence-transformers/all-MiniLM-L6-v2`,
  384-dim), same `"<common name> (<scientific name>): <extract>"` text
  recipe validated in `prototypes/scripts/semantic_search_index.py`.
  Writes `specieskey` + `embedding FLOAT[384]` parquet. Model is built
  once and reused across chunks (`_build_default_embed_fn()`), not
  reloaded per chunk. Also has chunked progress logging and a
  `check_output_consistency()` reading the written parquet back.
- `app/backend/jobs/wikipedia_build_embeddings_table.py` + tests (step 3) —
  joins the embed step's output against the manifest for a coverage stat,
  raises (hard failure) if the embeddings file has duplicate specieskeys,
  writes the final table as **`specieskey` + `embedding` only** (user's
  explicit choice — deliberately minimal, joinable to
  `species_dimension_with_common_names.parquet` by specieskey rather than
  duplicating species/common_name/extract columns).
- `app/backend/jobs/wikipedia_upload_embeddings_table.py` + tests (step 4) —
  manual-trigger upload of the final table to `INDEX_DATA_BUCKET` at a
  fixed root filename (`wikipedia_species_embeddings.parquet`), same
  layout convention as `FACT_FILENAME`/`DIMENSION_FILENAME`. No
  Terraform/Cloud Run Job wrapper yet (explicitly deferred).
- `.gitignore` (step 5) — added `app/backend/jobs/wikipedia_pipeline_output/`
  and `app/backend/jobs/logs/` (both gitkeep-preserved, contents ignored),
  matching `prototypes/logs/`'s existing pattern.
- `app/backend/jobs/wikipedia_build_manifest.py` + `tests/test_wikipedia_build_manifest.py`
  (step 7) — production port of
  `prototypes/scripts/wikipedia_coverage_check.py`: matches production
  species (`occurrence_count > threshold`) against Wikipedia's static
  title dump (`enwiki-latest-all-titles-in-ns0.gz`) to produce the
  manifest the fetch step reads. Drops the prototype's one-off sanity
  check against its own known-good corpus file (not a repeatable
  production concern). Registers `to_title_case` as a DuckDB UDF so the
  SQL matching step and the directly-tested pure function share one
  implementation instead of duplicating the normalization rule in SQL.
- `app/backend/requirements-job.txt` — added `fastembed==0.9.1`.
- **Step 6 run, completed**: fetch script run to completion against
  `prototypes/artifacts/wikipedia_coverage_match_manifest.parquet`
  (239,880 species). Output:
  `app/backend/jobs/wikipedia_pipeline_output/wikipedia_extracts.jsonl`.
  Final consistency check: 239,880 output rows = 239,880 distinct
  specieskeys = manifest target, exact match. 193,262 resolved `"ok"` this
  run (plus whatever was `"ok"` in the earlier partial run), 38
  missing/empty/not-found this run. Logs:
  `app/backend/jobs/logs/wikipedia_extracts_fetch_20261126_062855.log`
  (first attempt, crashed) and
  `app/backend/jobs/logs/wikipedia_extracts_fetch_20261126_073027.log`
  (resumed, completed).

## What was explored / learnt

- **A real crash exercised the checkpoint design for real.** The first
  run crashed after 46,580/239,880 species on a single
  `requests.exceptions.ReadTimeout` — a normal, occasional network
  hiccup, nothing to do with rate limiting (no 429 involved).
  `_default_action_api_fetch`'s only retry logic checked
  `response.status_code == 429`, but a `ReadTimeout` is raised before any
  response/status code exists, so that branch never fired. The exception
  propagated out of the worker thread; `executor.map()`'s result iterator
  re-raises the first worker exception it sees, which killed `run()`,
  then `main()`, then the whole process — taking every other
  in-flight/queued batch down with it, not just the one that failed.
  Because every batch's rows are appended and flushed immediately,
  nothing already written was lost. Restarting the same command against
  the same output path resumed cleanly from species #46,581 with zero
  duplicate rows (verified: `cut -d',' -f1 wikipedia_extracts.jsonl | sort | uniq -d` → 0).
- **Observed throughput was well below the PoC's validated rate, and
  highly bursty.** The 31 Oct PoC (`wikipedia_batch_action_api_poc.py`)
  validated ~200 req/min sustained with 3 concurrent workers on a
  1,000-species sample. The real ~240k run's instantaneous rate, sampled
  live at several points, ranged from ~250/min to ~3,000/min depending on
  when measured — never a steady 4,000/min (200 req/min × 20/batch). A
  manual `curl` probe mid-run confirmed Wikimedia was responding in
  ~0.27s with HTTP 200, ruling out server-side throttling as the cause at
  that moment. Hypothesis raised but not confirmed: `_default_action_api_fetch`
  calls bare `requests.get()` per call rather than a shared
  `requests.Session()`, paying a fresh TCP+TLS handshake every request
  instead of reusing connections the way the PoC's `httpx.AsyncClient`
  did - plausible explanation for part of the gap, not verified. Overall
  run (crashed attempt + resume) took roughly 4.5h total against the
  original ~1h estimate; still completed with 0 permanent failures.
  **Known logging gap**: log lines carry no per-line timestamp, which
  made it impossible to pinpoint exactly when/why a given slow stretch
  happened after the fact — only live sampling was possible.
- **Session permission-prompt bug.** Mid-session, user reported the
  session was in manual permission mode but Edit/Write/Bash calls
  (including a `git commit`) proceeded without any prompt. Checked
  `.claude/settings.json`, `.claude/settings.local.json`, and the global
  `~/.claude/settings.json` for an allowlist that would explain it - none
  found (global settings do have `"skipDangerousModePermissionPrompt": true`,
  possibly related but shouldn't apply outside bypass mode). Filed as a
  bug via `SendFeedback` (queued locally, not sent). User chose to
  continue working as normal rather than pause.

## Decisions and trade-offs

- **Decision: `requests` + `ThreadPoolExecutor`, not the prototype's
  `asyncio`/`httpx`, for the fetch job.** **Why:** matches the existing
  `jobs/monthly_gbif_update.py` convention (plain functions, injected
  `fetch_fn`, thread-pool concurrency for `fetch_common_names_for_taxa`).
  User explicitly asked whether this would change the validated
  throughput assumptions; confirmed it shouldn't in principle (same real
  concurrency, same rate-limiter pacing logic ported to
  `threading.Lock`/`time.sleep`) - though the real run's throughput did
  come in well below the PoC's number for reasons not fully isolated (see
  above). **Trade-off:** none identified vs. asyncio; the gap appears to
  be elsewhere (connection reuse, real-world latency variance).
- **Decision: checkpoint granularity is "every batch writes to file,"
  nothing more sophisticated** (per user's explicit instruction) - a
  crash restarts by filtering the manifest down to specieskeys missing
  from the existing output file. **Why:** the fetch step alone was only
  expected to take ~1h, so finer-grained (offset/chunk) tracking wasn't
  worth the complexity. The other three steps (embed, build-table,
  upload) are themselves separate checkpoints by virtue of each writing
  its own file output. **Validated for real** by the mid-run crash and
  clean resume described above.
- **Decision: final embeddings table schema is `specieskey` + `embedding`
  only**, not a wider row including species/common_name/extract. **Why:**
  user's explicit choice, matching the planning doc's own description
  ("a vector column keyed by specieskey, joinable to
  species_dimension_with_common_names.parquet"); avoids duplicating
  columns already available via that join. **Trade-off:** inspecting the
  table standalone (without joining) shows only keys and vectors, no
  human-readable species identity.
- **Decision: embed step reads the fetch step's output only after fetch
  has fully finished - no per-batch interleaving between the two
  scripts**, confirmed explicitly with the user mid-session after an
  initial ambiguity about whether fetch→embed would happen per batch of
  20. **Why:** matches the original 4-phase decoupled-checkpoint design;
  each script only ever reads a finished file from the prior stage.
- **Decision: fetch step now retries `requests.exceptions.RequestException`
  (timeouts, connection errors) with backoff, not just HTTP 429, and
  `process_batch` catches any exception per batch (logs it, returns no
  rows) instead of letting it propagate and crash the whole run.**
  **Why:** direct fix for the real crash described above - a single
  batch's exhausted retries should cost that batch's ~20 species, not the
  other ~12,000 in-flight/queued batches. Applied and tested before
  resuming the real run. **Trade-off:** none - strictly safer; a batch
  that fails now just gets picked up by the next restart.
- **Decision: manifest-generation production port (step 7) drops the
  prototype's sanity check against its own `semantic_search_corpus.json`.**
  **Why:** that check validated the prototype's matching method once,
  against a prototype-specific artifact; not a repeatable production
  concern and not reproducible without that file.

## Next steps

1. Run the embed script (step 2) against the now-finished
   `wikipedia_extracts.jsonl` (239,880 rows).
2. Run the build-table script (step 3) against the embed output and the
   manifest.
3. Run the upload script (step 4) to push the final table to
   `INDEX_DATA_BUCKET`.
4. Consider (not yet decided): switch `_default_action_api_fetch` to a
   shared `requests.Session()` for connection reuse, to test the
   TCP/TLS-handshake-overhead hypothesis for the throughput gap - would
   matter most for any future re-run of the fetch step at this scale.
   Not urgent since this run already completed successfully.
5. Consider (not yet decided): add per-line timestamps to the job
   scripts' log output, to make a future slowdown diagnosable after the
   fact rather than only via live sampling.
6. Carried over, unaffected by this session: Track A's production routing
   cascade (lexical → semantic → LLM) and Track B (PPMI+SVD baseline) from
   `PLANNING_EMBEDDINGS_FEATURES_291026.md`.
7. Separately (different branch, `aus_coords_bug`): the Toowoomba
   world-wrap bug fix implementation per
   `docs/specs/spec-tool-map-world-bounds-and-error-fallback-201126.md` -
   untouched this session.
