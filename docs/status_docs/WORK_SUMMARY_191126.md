# Work Summary — 19 November 2026

## What was built

- `prototypes/scripts/semantic_search_gcs_upload_fixture.py` — builds a
  239,880-row synthetic embedding table (real COL XR `specieskey`s from
  `wikipedia_coverage_match_manifest.parquet`, random unit `FLOAT[384]`
  vectors as placeholders) and uploads it to
  `gs://nature-quest-504414-index-data/test/synthetic_embeddings_239880.parquet`
  via the existing write-only `nature-quest-index-refresh` service account
  (impersonated, per `infra/gcs_index_data.tf`'s existing operator grant).
  Upload-only — no benchmark, no delete. Verified landed: 239,880 rows,
  370,437,863 bytes (353.28 MiB).
- `prototypes/scripts/semantic_search_gcs_latency_probe.py` — read-only
  benchmark against that already-uploaded fixture. Opens a GCS/httpfs
  connection the same way `services/species_index.py`'s
  `open_index_connection()` does in production (forced regardless of
  `K_SERVICE`), then times `array_cosine_similarity` ranking queries,
  cold (first query) vs. warm (median/p95 of 20 reps on the same shared
  connection), both unrestricted (full table) and restricted to a 2,000-key
  random subset (the realistic cell-restricted production shape).
- The test fixture (`gs://nature-quest-504414-index-data/test/synthetic_embeddings_239880.parquet`)
  is still live in the bucket as of session end — not yet deleted, left in
  place for further runs (e.g. a sorted-layout re-test).

## What was explored / learnt

- **First script attempt bundled upload + benchmark + cleanup into one
  script and failed mid-run** (a `FLOAT[]` vs `FLOAT[384]` cast mismatch on
  the embedding column read back from GCS — `array_cosine_similarity`
  requires an explicit `embedding::FLOAT[384]` cast on the *column*, not
  just the query vector, since `read_parquet` over httpfs returns it as the
  generic `FLOAT[]` type). The bundled script's `finally` block still ran
  cleanup correctly (deleted the test object), so nothing was left stranded
  in the bucket, but the whole upload→query→delete cycle (370MB) was wasted
  for zero signal. Split into the two separate scripts above as a direct
  result — upload and benchmark are now independently runnable and
  inspectable (`gcloud storage ls` the object between steps), matching the
  user's explicit preference (see Decisions below).
- **Real GCS-backed benchmark results** (239,880 rows, 368MB raw vector
  storage):

  | Query | Cold (1st) | Warm p50 | Warm p95 |
  |---|---|---|---|
  | Full-table scan | 11,173.6 ms | 975.9 ms | 1,210.3 ms |
  | Subset-restricted (2,000 keys) | 616.1 ms | 623.2 ms | 669.8 ms |

- **Pruning analysis (the key mechanism, worked through with the user in
  detail):**
  - Parquet row-group pruning works when a `WHERE` filter is on a stored
    column's *value* and row groups are laid out so matching rows cluster
    together — min/max stats per row group let the reader skip whole
    groups' byte ranges. This is why the fact-table `cell IN (...)` lookup
    is fast (confirmed elsewhere: skips to ~1/212 row groups).
  - `array_cosine_similarity` itself is **structurally unprunable** — the
    sort key is computed from the embedding vector against an arbitrary
    query vector at query time, so no stored min/max statistic can bound it
    in advance. Every row's score must be computed, full stop, regardless of
    any filter.
  - The `specieskey IN (...)` subset filter *is* a real column-value filter
    and could prune in principle, but this fixture's rows are in roughly
    the manifest's original order (not sorted by `specieskey`) — a random
    scatter of 2,000 keys out of 239,880 very likely hits at least one match
    in almost every row group anyway, so pruning barely triggers. The
    observed subset speedup (616–623ms vs. 976ms–11.2s) is real but far
    short of the ~99% reduction true pruning at 0.8% selectivity would give
    — most likely a mix of a smaller post-fetch compute step and some
    partial/incidental row-group skipping, not "scanning less of the table."
    This was an initial over-claim corrected mid-session (first said
    restriction "barely helps"; the data shows partial but real help, with
    an unverified residual gap).
  - **Corrected the user's natural-but-wrong hypothesis**: a smaller
    candidate-cell area does *not* proportionally reduce this cost at the
    current unsorted layout, because the dominant cost is network transfer
    of the embedding column, not row-group skipping — a 50-species cell and
    a 5,000-species cell currently pay roughly the same fallback latency.
    Sorting the real embedding table by `specieskey` (so pruning can
    actually trigger, the way `cell`-sorting already does for the fact
    table) is the untested lever that could change this, flagged as a
    follow-up, not yet run.
- **Routing cascade cost implication worked out with the user**: for
  "birds" (LLM resolves a taxon) — LLM call + prunable fact-table lookup
  only, under ~1.1s total, matches what's already confirmed in production.
  For "a colourful animal" (LLM declines, falls to semantic fallback) — LLM
  call (~300–800ms) + `species_in_cells()` fact lookup (~100–200ms) +
  `embed_query()` (~25ms) + this session's `semantic_rank_in_cells()`
  measurement (~620ms, using the realistic subset-restricted number, not
  the full-table one) ≈ roughly 1.0–1.6s total. Caveat: measured as a
  repeated-query benchmark on one shared connection (best-case warm
  condition) — in real traffic this ~600ms+ recurs on every fallback
  request, since there's no cross-request caching of the embedding file's
  content the way credentials are cached.

## Decisions and trade-offs

- **Decision: split the GCS fixture-upload step from the benchmark step
  into two separate scripts, with no auto-delete.** **Why:** the first
  attempt bundled upload→query→cleanup into one script; when the query
  failed, the whole 370MB upload/delete cycle ran for zero signal, and the
  user had no checkpoint to verify the upload before the benchmark touched
  it — explicit user feedback mid-session ("bit frustrated that coupled the
  upload and test in one script"). **Trade-off:** an extra manual step
  (and an extra `gcloud storage rm` to run) versus a single
  run-and-forget script, accepted deliberately for inspectability.
- **Decision: test fixture uploaded to `test/` prefix inside the real
  production bucket (`nature-quest-504414-index-data`), not a separate
  throwaway bucket.** **Why:** needed a real network-backed GCS/httpfs read
  to answer the actual question (does the scan behave the same way
  production's reads do); offered a separate-bucket alternative, user
  confirmed proceeding with the same-bucket `test/` prefix approach.
  **Trade-off:** real (if small — cents) GCS cost, and a test object
  temporarily present in the same bucket as production's real fact/dimension
  data, mitigated by the `test/` prefix and (eventual) deletion.
- **Decision: used the subset-restricted (2,000-key) benchmark number, not
  the full-table number, as the representative production-fallback
  latency estimate.** **Why:** `semantic_rank_in_cells()` in production is
  never called unrestricted — it's always scoped to `candidate_keys` from
  `species_in_cells()` for the drawn polygon, so the subset shape is what a
  real request actually runs.

## Blockers

None blocking. One real open risk, sharpened this session rather than
resolved: the semantic-fallback path currently costs ~600ms+ **regardless of
candidate-cell-area size**, because the embedding table isn't sorted in a
way that lets `specieskey IN (...)` filtering actually prune row groups —
unlike the fact table, this won't get cheaper on its own as implemented
today.

## Next steps

1. **Confirm the production evaluation approach** — lift
   `test_routing_cascade_eval.py` into `app/backend/tests/evals/`; needs
   the compound-query routing-instability decision and a real
   confidence-threshold calibration. (Decided next, ahead of item 2.)
2. **Design/build the real Wikipedia batch extract + embedding job**
   against the confirmed ~240k manifest — planning for this starts right
   after item 1.
3. Implement end-to-end in production: model loading, routing logic,
   evals — now informed by this session's real latency numbers for the
   fallback path.
4. Scope/build the Cloud Run heartbeat trigger for cold-start mitigation.
5. Stage and commit the prior session's uncommitted prototype files,
   plus this session's two new scripts
   (`semantic_search_gcs_upload_fixture.py`,
   `semantic_search_gcs_latency_probe.py`) — all still untracked.

**Dropped this session**: further sorted-layout pruning investigation
(sorting the embedding table by `specieskey` to test real row-group
pruning) — explicitly decided not to pursue as a next step. The test
fixture (`gs://nature-quest-504414-index-data/test/synthetic_embeddings_239880.parquet`)
has been deleted from the bucket; the local copy was also removed.
