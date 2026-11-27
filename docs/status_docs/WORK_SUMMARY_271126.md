# Work Summary — 27 November 2026

Branch: `build_wiki_embeddings_table`. Ran the remaining steps of the
Wikipedia-embeddings pipeline (`PLANNING_EMBEDDINGS_FEATURES_291026.md`
Track A / `WORK_SUMMARY_261126.md` next steps) to completion: embed, build
table, upload to production GCS, then sense-checked the live result.

## What was built

- **Embed step checkpointing** (`app/backend/jobs/wikipedia_embed_extracts.py`
  + `tests/test_wikipedia_embed_extracts.py`) — before running the real
  ~3h embed job, added crash-resilience and bounded memory, since the
  original design held all ~240k embeddings in Python memory and only
  wrote once at the end (no incremental checkpoint, unlike the fetch
  step's JSONL-append design). Parquet has no native append, so the fix
  writes each chunk (2,000 rows) to its own UUID-named file in
  `{embeddings_output_path}.chunks/`, resumes by reading already-
  checkpointed specieskeys from that directory
  (`read_embedded_specieskeys`, `records_to_embed`), and merges every
  chunk into the single final output via one streamed DuckDB `COPY`
  (`merge_chunks_to_final`) - disk-to-disk, never materializing all rows
  in Python. New tested functions: `write_chunk_parquet`,
  `read_embedded_specieskeys`, `records_to_embed`, `merge_chunks_to_final`.
  `run()` gained a `chunk_dir` parameter (default derived from
  `embeddings_output_path`). All 18 embed-step tests pass; full backend
  suite (288 tests) passes.
- **Real embed run, completed**: ran against the finished
  `wikipedia_extracts.jsonl` (239,880 rows). Timed a single 2,000-row
  chunk first (21.1 rows/sec measured) to get a real estimate (~3.15h)
  before committing to the full run, rather than relying on the 31 Oct
  PoC's 25ms/query figure (single short-query inference, not representative
  of batch-encoding full Wikipedia extracts). Longest real extract: 7,885
  chars (*Tactostoma macropus*, specieskey `54GFZ`); mean 310 chars,
  median 213. Logged to
  `app/backend/jobs/logs/wikipedia_embed_extracts_20261127_092507.log`
  (same stdout-redirect convention as the fetch step's logs), run via
  `nohup ... &` in the background per the user's explicit choice (noted:
  user would have preferred a separate foreground shell rather than
  background execution, but accepted it for this run). Actual runtime
  ran longer than the sample-based estimate - 29.2% at 1h elapsed, 62.5%
  at 3h24m, fully complete sometime before the ~5h mark. Final result:
  **239,834/239,880 embedded** (46 skipped: 42 `empty_extract`, 4
  `missing` - confirmed via direct count of the fetch output, correcting
  an earlier approximate figure of "239,842/38" that was derived from a
  partial-run-only line in `WORK_SUMMARY_261126.md`). Consistency check
  passed: output rows = distinct specieskeys = embeddable target, exact
  match.
- **Embedding quality sense-check** (throwaway script, not committed,
  built in the scratchpad): structural integrity (239,834 rows, 239,834
  distinct keys, all 384-dim, zero NaN/Inf/null, all vector norms exactly
  1.0000 - confirms proper L2 normalization) and a similarity check using
  exact scientific names (not loose substring matching, which initially
  mismatched "lion"→jellyfish and "tiger"→butterfly before being
  corrected). Same-genus pairs (Tiger/Lion *Panthera*, two *Quercus* oaks,
  two *Turdus* thrushes) all scored 0.67-0.70 cosine similarity; the
  single lowest pair in the whole set (Lion vs. European Oak, 0.058) was
  the most biologically unrelated - embedding space orders species
  sensibly.
- **`app/backend/jobs/wikipedia_build_embeddings_table.py` run**: against
  the embed output and the production manifest
  (`prototypes/artifacts/wikipedia_coverage_match_manifest.parquet`).
  99.98% coverage (239,834/239,880), no duplicate-specieskey failures.
  Output: `app/backend/jobs/wikipedia_pipeline_output/wikipedia_species_embeddings_table.parquet`.
- **`app/backend/jobs/wikipedia_upload_embeddings_table.py`** — added
  service-account impersonation support
  (`WIKIPEDIA_UPLOAD_IMPERSONATE_SERVICE_ACCOUNT` env var, new
  `build_client` function) so a manual run can impersonate the
  write-only `index_refresh` identity (ADR-016 / `infra/gcs_index_data.tf`)
  rather than needing standing write access on the operator's own
  account, or a one-off throwaway script. Confirmed the operator account
  (`alex.handy.research@gmail.com`) already holds
  `roles/iam.serviceAccountTokenCreator` on
  `nature-quest-index-refresh@nature-quest-504414.iam.gserviceaccount.com`
  via `gcloud iam service-accounts get-iam-policy`. Default behavior
  (env var unset) is unchanged. Existing tests untouched/still passing
  (the `run(client, ...)` seam they cover didn't change).
- **Real upload, completed**: table uploaded to
  `gs://nature-quest-504414-index-data/wikipedia_species_embeddings.parquet`
  (352.9 MiB / 370,044,789 bytes), confirmed via `gcloud storage ls -l`.
- **Live-bucket verification** (throwaway scripts, scratchpad, not
  committed): confirmed the uploaded file is readable and correct via
  the exact same DuckDB httpfs + GCS-HMAC-secret path production uses
  (`services/species_index.py`'s `open_index_connection`) rather than
  via `gsutil`/`gcloud storage cat`. Row/key counts matched exactly;
  Lion vs. Tiger similarity came back bit-identical to the pre-upload
  check (0.6972). Point-lookup latency: ~4.3s on the very first query
  against a fresh connection (likely TLS/connection warm-up), then
  0.12-0.23s on repeated point lookups once warm. One-time httpfs/secret
  setup: ~5.1s (cached per-container for the whole process lifetime in
  production, per the existing caching design - not a per-request cost).
- **Real semantic top-5 search against the live bucket** (throwaway
  script, scratchpad): embedded free-text queries locally with the same
  fastembed MiniLM model, then ran `array_cosine_similarity` entirely in
  SQL against all 239,834 remote rows (`CAST(embedding AS FLOAT[384])`
  needed explicitly - `read_parquet` doesn't preserve the fixed-size
  array type automatically, DuckDB errors without the cast). First query
  cold: 8.6s; subsequent queries once warm: ~1.2-1.3s each - a full
  linear scan with no index. Confirmed one exact reproduction of the 31
  Oct report's known failure mode: "something that flies" surfaces
  literal flies (*Eristalis*, *Condylostylus*), not flying animals
  generally - same issue, now confirmed at full 239,834-species scale
  rather than the original 100-species sample.

## What was explored / learnt

- **Parquet has no native append**, unlike JSONL - any "checkpoint
  per chunk" design for a parquet output has to write one file per
  chunk and merge at the end, not append to one file in place. This
  shaped the embed-step checkpointing fix above.
- **Tokenizer truncation (256 tokens, `max_length=256`) doesn't eliminate
  the cost of long inputs**: the tokenizer still has to WordPiece-tokenize
  the *entire* raw string before truncating the result, and `BatchLongest`
  padding means one long outlier in a batch inflates padding cost for
  every other sequence in that batch. Pre-truncating raw characters before
  tokenization would save the wasted tokenizer CPU pass on long outliers,
  but would *not* fix the padding issue (padding is keyed off tokenized
  length, already capped at 256 regardless of pre-truncation). Fixing the
  padding issue specifically would need sorting/bucketing by length before
  chunking - a bigger, separate change. Given only ~5 of 239,834 extracts
  exceed 5,000 chars, judged not worth implementing for this one-off run;
  parked as a known, minor, optional optimization.
- **GCS bucket write access requires impersonation, not a wrapper script**:
  initially built an inline Python wrapper using impersonated credentials
  to do the real upload once, but the user correctly pushed back - that
  wrapper works once and is gone, not a repeatable mechanism. Fixing the
  actual job script (optional impersonation support, see above) is the
  right call since every manual run of this script will need it until the
  deferred Phase 4 automated job exists with the identity attached
  directly.
- **DuckDB's `array_cosine_similarity` needs an explicit `FLOAT[384]` cast
  on the column read back from parquet** - without it, DuckDB infers a
  variable-length `FLOAT[]` list type and the function call fails with a
  `BinderException` ("No function matches... You might need to add
  explicit type casts").
- **Confirmed the production routing-cascade design is LLM-first, not
  lexical-first**: `prototypes/scripts/routing_cascade.py` /
  `server_routing_cascade.py` already prototype and validate LLM-resolves-
  taxon-filters-first, semantic-fallback-only-when-empty - this
  supersedes the planning doc's originally-described lexical-first
  ordering for production. (Corrected mid-session after initially
  mischaracterizing it as lexical-first in a latency discussion.)

## Decisions and trade-offs

- **Decision: embed step checkpoints per-chunk to a directory + merges
  once at the end, rather than appending to one file per chunk.** **Why:**
  parquet has no append primitive (see above); appending would mean
  rewriting the whole accumulated file on every chunk (O(n²) I/O across
  the run). **Trade-off:** a new failure point (the final merge itself can
  fail), but low-stakes since the chunk directory is untouched by the
  merge and a failed merge can just be re-run; also leaves ~120 small
  files and briefly ~2x disk usage during the merge window - judged
  acceptable at this scale (~370MB final output).
- **Decision: did not implement temp-write + atomic-rename for individual
  chunk files**, despite it being a real crash-safety gap (a crash mid-
  write of one chunk file could leave a truncated file miscounted as
  checkpointed). **Why:** user's explicit call - "over management for
  this one off job." Noted as a known, accepted gap, not fixed.
- **Decision: did not pre-truncate raw extract text before tokenization.**
  **Why:** real measured impact is small (~5 of 239,834 rows exceed 5,000
  chars) and doesn't fix the batch-padding cost anyway (see above) -
  user and assistant agreed this was a minor, optional optimization not
  worth the added code for a one-off job.
- **Decision: fixed the real upload script to support service-account
  impersonation (`WIKIPEDIA_UPLOAD_IMPERSONATE_SERVICE_ACCOUNT` env var)
  rather than using a one-off inline wrapper.** **Why:** user explicitly
  pushed back on the wrapper approach - every future manual run needs
  this same impersonation, so it belongs in the reusable script, not a
  throwaway. **Trade-off:** none identified; default behavior (env var
  unset) is unchanged, so this doesn't disturb the other jobs' plain-
  `storage.Client()` pattern.
- **Decision: for the semantic-fallback step's "should we show results at
  all" problem (open question in `PLANNING_EMBEDDINGS_FEATURES_291026.md`),
  try a plain similarity-threshold cutoff first before reaching for a
  fast LLM-judge model** (binary yes/no relevance check per candidate).
  **Why:** zero added latency/cost per query vs. a second network round-
  trip for every semantic-fallback query; only escalate to the judge-model
  approach if the threshold-only baseline proves too imprecise in
  practice. Not yet built - this is a decision about the *order* to try
  approaches in, not an implementation.

## Next steps

1. **Design/build the production routing cascade** (LLM-first, semantic-
   fallback-second, per the already-prototyped `routing_cascade.py`
   pattern - not the planning doc's originally-described lexical-first
   order). This includes wiring the now-live
   `gs://nature-quest-504414-index-data/wikipedia_species_embeddings.parquet`
   into `services/species_index.py` alongside the existing fact/dimension
   parquet reads (`open_index_connection`, `query_species_for_filter`) -
   not a separate task, just where the semantic-fallback branch's data
   access would live. The open problem to resolve: when to suppress
   semantic-fallback results entirely (no good match) - try a
   similarity-threshold cutoff first; only add a fast LLM-judge model
   (binary relevance check) if the threshold approach proves too
   imprecise.
2. Revisit the embed step's known, parked minor gaps if this pipeline is
   ever re-run at this scale again: no temp-write/atomic-rename on chunk
   files (accepted gap), no raw-text pre-truncation before tokenization
   (minor, optional).
3. Carried over, unaffected by this session: Track B (PPMI+SVD baseline)
   from `PLANNING_EMBEDDINGS_FEATURES_291026.md`.
4. Separately (different branch, `aus_coords_bug`): the Toowoomba
   world-wrap bug fix per
   `docs/specs/spec-tool-map-world-bounds-and-error-fallback-201126.md` -
   untouched this session.
