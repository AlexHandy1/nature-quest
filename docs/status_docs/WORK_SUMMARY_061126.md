# Work Summary — 06 November 2026

## What was built

- `prototypes/scripts/semantic_search_index_colxr.py` — re-keys the existing 100-species
  embedding corpus by COL XR id (resolved via a join on scientific name against
  `DIMENSION_PARQUET_PATH`), fixing a real ID-space mismatch (see below). 98/100 species
  resolved (dropped: `Strigops habroptilus`, `Rafflesia arnoldii` — no dimension-table
  match). Builds `prototypes/artifacts/semantic_search_colxr.duckdb` (fastembed/ONNX
  embeddings, table `species`, `specieskey` is now the COL XR id).
- `prototypes/scripts/routing_cascade.py` — pure logic module implementing the LLM-first /
  semantic-fallback routing cascade: `resolve_species_for_query()` calls
  `resolve_taxon_filters()` first; only on `taxon_filters == []` does it run
  `species_in_cells()` (new — all species present in candidate cells, unfiltered by taxon,
  doesn't exist in production) and `semantic_rank_in_cells()` (cosine-ranks only that
  cell-restricted candidate set, never a global top-k filtered down afterward). Returns
  branch taken, species, and a per-stage latency breakdown.
- `prototypes/scripts/server_routing_cascade.py` + `prototypes/web/index_routing_cascade.html`
  — thin Flask wrapper (port 5059) + browser UI, fixed Retiro Park polygon (same as
  `app/backend/tests/test_query.py`), shows which branch fired, the resulting species, and
  the latency breakdown per stage.
- `prototypes/scripts/conftest.py` — registers the `eval` pytest marker for this
  directory (mirrors `app/backend/pytest.ini`).
- `prototypes/scripts/test_routing_cascade_eval.py` — eval suite written in the exact
  shape of `app/backend/tests/evals/test_taxon_resolution_eval.py` /
  `test_full_pipeline_eval.py`, intended to be lifted into `app/backend/tests/evals/`
  once this design is confirmed. 10 tests, all passing against real OpenRouter LLM calls
  and live GBIF verification (no mocking): 3 regression tests confirm the LLM-resolved
  branch is unchanged from production; 5 cover cascade correctness (semantic fallback
  triggers on decline, grounding to the polygon, descending rank order, no fallback when
  a resolved taxon has zero local occurrences); 2 verify actual semantic *species output*
  against live GBIF class data.
- `prototypes/scripts/wikipedia_coverage_check.py` — downloads
  `enwiki-latest-all-titles-in-ns0.gz` once (`prototypes/scratch/`, gitignored, ~110MB,
  19,353,576 titles) and does a fully local, offline DuckDB title match against the full
  production dimension/fact tables — zero per-species Wikipedia API calls. Writes
  `prototypes/artifacts/wikipedia_coverage_check_results.json` (summary stats) and
  `prototypes/artifacts/wikipedia_coverage_match_manifest.parquet` (239,880 rows:
  specieskey, species, common_name, total_occurrence, matched_title,
  matched_on_scientific_name) — the real starter manifest for the batch embedding job's
  species-set scope.
- None of this session's new files are committed yet (all untracked).

## What was explored / learnt

- **ID-space mismatch found and fixed**: `semantic_search_corpus.json`'s `specieskey` is
  GBIF's numeric backbone `usageKey` (via the live `species/match` API, per that script's
  own docstring) — NOT the Catalogue of Life (COL XR) id used as `specieskey` in
  production's fact/dimension parquet tables (alphanumeric, e.g. `4HPXM`). These are
  different ID spaces with no direct join. This would have silently blocked the
  cell-restriction join (the core design constraint) had it not been caught before
  building `routing_cascade.py`. Fixed by resolving each corpus species' COL XR id via a
  one-time join on scientific name against the dimension table (107 raw matches,
  deduplicated to 98 after dropping 2 unresolved names) — this is also the approach the
  real production batch job should use (start from the dimension table's own species
  list + COL XR keys, not a fresh `species/match` call per species).
- **Routing design decided: LLM-first, sequential.** Always call `resolve_taxon_filters`
  (today's production call); only fall back to semantic search when it returns
  `taxonFilters: []`. A concurrent/parallel-dispatch alternative (fire both the LLM call
  and semantic search at once) was explicitly considered and rejected: semantic search is
  fast (~25ms warm, confirmed again this session), so concurrency would add unconditional
  extra compute to every request (including the majority that resolve cleanly via LLM)
  for a marginal latency win concentrated only on the minority fallback path.
- **Reproduced and diagnosed the "swims → butterfly" failure live.** "something that
  swims" ranks Red Admiral (butterfly) above the one real local match (Common carp,
  0.166) in Retiro Park. Root cause predates this session — the 31 Oct experiment already
  showed this exact failure at full-corpus scale (Red Admiral top-1 at 0.281, with two
  marine fish rescuing the fish category further down). Cell-restriction makes it worse:
  Retiro doesn't have either marine fish species, so only the much-lower-scoring carp is
  left to represent "swims" locally. All top-10 scores in this run sat in the 0.12-0.28
  range, overlapping the 31 Oct report's out-of-domain nonsense-query baseline
  (0.11-0.225) — concrete evidence that the parked confidence-threshold design question
  is load-bearing, not polish: without it, cell-restriction can take an already-marginal
  query and make the wrong answer look more confident by starving out the few
  locally-present species that would have been right.
- **The 31 Oct report's threshold recommendation (~0.25-0.30 top-1 floor) changes role
  under the new cascade.** Originally meant "escalate to the LLM" (a step that hadn't run
  yet); under LLM-first, the LLM has already run and declined before semantic search
  executes, so the same floor would now have to mean "show no results," a
  higher-stakes use of the same unvalidated number.
- **Turtles investigation**: confirmed "turtles" still resolves correctly via the
  LLM-resolved branch (9 real turtle species found in Retiro's cells, unchanged from
  production). Separately confirmed none of the 6 reptile/turtle species in the 98-species
  embedding corpus are present in Retiro's candidate cells at all — correct behaviour
  (cell-restriction working as designed), but also highlights that only 21/98 corpus
  species are even locally present in Retiro, since the corpus is a small, globally
  curated sample, not a locally representative one.
- **Major finding: `resolve_taxon_filters()` is not deterministic on compound
  taxon+thematic queries.** "birds and pollinators" called 4 times with byte-identical
  input produced 3 different outputs: `[{Aves},{Insecta}]` (fully correct),
  `[{Aves}]` alone (silently drops pollinators), and `[]` entirely (declines, routes to
  semantic instead). This is more serious than the previously-flagged "LLM might
  confidently guess wrong instead of declining" risk — it's routing instability on
  identical input, not one stable, predictable failure shape. A planned eval test for
  this case was written then removed rather than pinned to one assertion, since there
  was no single correct behaviour to assert against.
- **Eval-writing mistakes caught and corrected in-session**: "birds of prey," "colourful
  birds," and "a squirrel" were initially chosen as semantic-fallback test queries, but
  all three contain a literal taxon noun the LLM resolves directly via the taxon path —
  confirmed when the user independently tested "a squirrel" and caught it. Replaced with
  "something that flies" and "creatures with wings," both confirmed to decline
  (`taxon_filters == []`) via direct calls before being hardcoded into test assertions.
  "insects that visit flowers" was tried and dropped — its top-5 included two plants
  (Dandelion, Daisy), the same "topically adjacent but wrong category" failure mode as
  the swims/butterfly case.
- **Wikipedia coverage check**: of 917,827 species with `occurrence_count > 1`, only
  26.1% (239,880) have a matching English Wikipedia title (scientific or common name) via
  exact local title-dump match — scientific-name match does almost all the work (26.1%
  vs. 2.2% common-name-only). Method recall validated first: 100/100 of this session's
  known-good species titles were found in the dump, so the low rate reflects genuine
  Wikipedia coverage gaps on the GBIF long tail (misses sample: obscure species like
  *Gasteruption forticorne*, *Xylotrupes beckeri*), not a flaw in the matching method.
  This is a conservative/lower-bound estimate — misses multi-hop redirects and
  disambiguated titles (e.g. "Oak (plant)") — deliberately not pursued further this
  session per the user's call that scientific-name matching at ~240k is "enough coverage
  as a starter."
- **Reviewed `REPORT_duckdb_caching_latency_experiment_221026.md` for transferable
  production-latency evidence**, since the user's primary concern closing the session was
  how the ~240k-row embedding table's size affects real production query latency. Findings
  that transfer: a **shared, reused DuckDB connection** (not per-request setup) cut warm
  `read_parquet()` query time on the existing GCS-backed fact/dimension table (917k
  species, 221M fact rows) from ~760-1,006ms to ~189-220ms — a 4-5x win, the single
  biggest lever found, bigger than credential-caching alone. A cold container's first
  query pays a separate, much larger one-time cost (~5.5s). **The gap this doesn't
  close**: that report's fast warm numbers are for row-group-prunable filtered lookups
  (`WHERE cell IN (...)`, confirmed elsewhere to skip to 1/212 row groups) — semantic
  ranking can't prune the same way, since `array_cosine_similarity` must read and compute
  against every candidate row's `FLOAT[384]` vector before sorting/limiting, regardless of
  whether `WHERE specieskey IN (candidate_keys)` helps skip non-candidates. Neither this
  session's `routing_cascade.py` timings (`semantic_rank_in_cells` ~5ms) nor the existing
  `semantic_search_scale_probe.py` (synthetic, capped at 106k) test this under real GCS
  httpfs reads — both are local/in-memory only. **Conclusion: strong evidence the
  connection-reuse architecture scales for pruned lookups at far bigger scale than 240k
  rows; zero direct evidence yet for how a similarity-ranking scan specifically behaves
  over network-backed storage as the embedding table grows** — this is the real open
  question behind the table-size/latency concern, not table size in isolation.

## Decisions and trade-offs

- **Decision: routing cascade is LLM-first and sequential, never concurrent.** **Why:**
  the LLM call already happens on every request today, so triggering semantic fallback
  off its existing `taxonFilters: []` output costs nothing extra; concurrency would cost
  unconditional compute on the majority path for a marginal win on the minority path.
  **Trade-off:** the empty-list signal is binary (no confidence score) and can't detect
  the LLM confidently guessing wrong instead of declining — accepted as a risk to measure
  via the eval harness, not fixed in this design pass.
- **Decision: re-key the embedding corpus by COL XR id, not GBIF's numeric usageKey.**
  **Why:** production's fact/dimension tables are COL-XR-keyed; without this, the
  cell-restriction join is structurally impossible. **Trade-off:** 2/100 corpus species
  don't resolve to a dimension-table row and are dropped from the re-keyed store.
- **Decision: the mixed taxon+thematic compound-query case ("birds and pollinators") is
  parked as a known limitation, with no eval test pinned against it.** **Why:** once
  routing instability was discovered (3 different outputs across 4 identical calls),
  there was no single stable behaviour left to assert against — pinning any one outcome
  would just encode a coin-flip as if it were a spec. **Trade-off:** this leaves a real,
  user-facing inconsistency un-monitored until the eval harness or routing design is
  revisited specifically for this case.
- **Decision: use a static Wikipedia title dump for the coverage check, scientific-name
  matching only, no redirect-dump follow-up for now.** **Why:** avoids ~900k individual
  HTTP round trips (hours, per the 31 Oct report's per-request timing); one ~110MB
  download + local DuckDB join does the full check in under a minute of compute, and
  scientific-name matching alone already gets to ~240k, which the user judged sufficient
  coverage to start from. **Trade-off:** conservative/lower-bound estimate — the real
  hit rate is somewhat higher than 26.1% once multi-hop redirects and disambiguated
  titles are accounted for, deliberately left unquantified for now.

## Blockers

None blocking. Two open items carried forward as risks rather than blockers: the
confidence-threshold calibration (needs a larger labelled set than the 24-scenario 31 Oct
corpus) and the compound-query routing instability (needs either a prompt fix, a
detection step, or an explicitly monitored/accepted limitation).

## Next steps

### Simplified

1. **Test query performance in a production-like environment for ~240k species** — to
   green-light the batch size and the GCS/httpfs-backed approach before building anything
   further on top of it.
2. **Build/run the real Wikipedia extract + embedding job** against the confirmed ~240k
   manifest.
3. **Co-pilot the full production build**: routing → evals → infra → deploy.
4. **Scope/build a Cloud Run "heartbeat" trigger** to negate/mitigate the cold-start
   issue we already know exists and will get worse once more memory (the embedding model
   + the larger embedding table) needs to be loaded at container startup.

### Detailed

1. **Test the real production-shaped latency question: does a semantic similarity scan
   stay fast as the embedding table grows, when read over GCS/httpfs like the existing
   fact/dimension tables?** Build a synthetic, real-scale embedding table from the new
   239,880-row manifest (`prototypes/artifacts/wikipedia_coverage_match_manifest.parquet`)
   — same real COL XR keys, placeholder/random vectors — but store and read it the same
   way production would (GCS parquet/DuckDB file behind httpfs with a shared, reused
   connection, not a local in-memory table), then re-run `species_in_cells()` /
   `semantic_rank_in_cells()` against it, measuring cold vs. warm per
   `REPORT_duckdb_caching_latency_experiment_221026.md`'s own discipline
   (`timed_checkpoint`). This is deliberately narrower than "does the embedding table fit
   in memory" — the existing report already shows the connection-reuse architecture
   scales fine for *pruned, filtered* lookups at far bigger scale (917k species, 221M
   fact rows, ~200ms warm); what's untested is whether an unprunable
   `array_cosine_similarity` scan over a growing `FLOAT[384]` column behaves the same way
   under network-backed reads, which is the real question behind the table-size/latency
   concern. Cheap to run (no Wikipedia fetching needed) and directly de-risks the batch
   size/approach before committing further. Exact storage math at this scale: 239,880 ×
   384 × 4 bytes ≈ 351MB (replaces the earlier ~163MB/~1.4GB extrapolated guesses).
2. **Confirm the production evaluation approach**, extending
   `test_routing_cascade_eval.py`'s shape for lifting into `app/backend/tests/evals/`.
   Needs: a decision on how to handle the compound-query routing instability, and a real
   confidence-threshold calibration using a larger labelled set.
3. **Design and build the real Wikipedia batch extract + embedding job** against the
   confirmed ~240k-species manifest (no longer the vague "106k vs 918k" framing), reusing
   the `monthly_gbif_update_job.tf` Cloud Run Job pattern. Real batch-time estimate at
   this scale: ~14.5h sequential / ~1.5h at 10x concurrency (31 Oct report's measured
   rates × 239,880 requests).
4. **Implement end-to-end in production**: wire in fastembed/ONNX model loading
   (confirmed prior session), the LLM-first/semantic-fallback routing logic, and the
   confirmed evals.
5. **Stage and commit this session's prototype files** — `routing_cascade.py`,
   `semantic_search_index_colxr.py`, `server_routing_cascade.py`,
   `index_routing_cascade.html`, `conftest.py`, `test_routing_cascade_eval.py`,
   `wikipedia_coverage_check.py`, and their artifacts — currently untracked.
6. **Scope/build a Cloud Run heartbeat trigger to mitigate cold start.**
   `REPORT_duckdb_caching_latency_experiment_221026.md` already measured this app's
   current cold-container cost directly: ~19s page-load cold start dominated by Cloud Run
   container provisioning, plus a separate ~5.5s first-query-after-boot cost for the
   GCS/httpfs parquet read, both independent of request-level caching fixes and both
   things only `min_instances ≥ 1` (or an equivalent keep-warm mechanism) removes. Adding
   the embedding model load (fastembed/ONNX, ~1.1s measured this session in
   `server_routing_cascade.py`'s own startup) and a larger embedding table to the startup
   warming sequence adds more to load before a cold container can serve traffic, not
   less — making this a known, worsening cost rather than a new or hypothetical one. Scope
   a periodic heartbeat (e.g. Cloud Scheduler hitting a health/warm endpoint on an
   interval shorter than Cloud Run's idle-scale-to-zero window) as a cheaper alternative
   or complement to `min_instances ≥ 1`, and decide which approach before the embedding
   model/table are added to the startup path.
