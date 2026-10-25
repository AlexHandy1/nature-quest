# ADR-020: The Common-Names Cache Records Confirmed-Empty Lookups, Not Just Hits

## Status
Accepted

## Date
2026-10-25

## Context

`monthly_gbif_update.py`'s common-names step looks up each top-N
specieskey (the smallest set covering 99% of cumulative occurrence
volume, currently ~106,247 species) against ChecklistBank, and persists
the result in `common_names_cache.parquet` so future runs don't
re-fetch species already looked up. `new_entrants()` decided what to
fetch by checking whether a specieskey was a *key* in the cache dict -
but the cache (and `merge_into_cache`/`build_common_name_map`) only
ever wrote an entry when ChecklistBank actually returned a name.
A specieskey ChecklistBank genuinely has no English name for was
therefore indistinguishable from one never looked up at all - both
were simply absent.

This was invisible until a real run (24 Oct 2026, the pipeline's first
real execution after automation) exposed it at scale: the top-N set
needs 106,247 species, but only 59,199 (4.48% of the full 1.32M-species
dimension) have ever had a name found - a figure that matches almost
exactly the original 20 Oct 2026 manual batch run's own measured ~55.7%
hit rate for this same ranking method (`WORK_SUMMARY_201026.md`). The
other ~44.3% (~47,070 species) aren't failures to find a name -
ChecklistBank simply doesn't have an English vernacular name for most
long-tail species. Every monthly run was re-attempting real HTTP
lookups for that same ~47k-species batch and discarding the (correctly
empty) result every time, with zero chance of ever resolving itself,
since the underlying occurrence data for an already-published GBIF
release doesn't change between runs (confirmed: the 24 Oct run's
guardrail check showed byte-identical counts, `2871617647 ->
2871617647` etc., not just "near-zero change").

## Decision

`fetch_and_merge_common_names()` now records `common_name = None` for
every entrant that was actually attempted but got nothing back, not
just the ones that succeeded. This is a real, present row/dict entry -
distinct from "never attempted" (absent) - so `new_entrants()`'s
existing `key not in existing_cache` check (unchanged) correctly treats
it as already-covered.

`None` was chosen deliberately over an empty string or a separate
schema column: it's already exactly the value the dimension file and
frontend expect for "no common name" (confirmed by tracing the full
path: `join_common_names`'s `LEFT JOIN` already produces a clean `NULL`
on the dimension table either way, `ResultsPanel.tsx`'s
`s.common_name ?? s.species` and `NarrationControl.tsx`'s identical
pattern only fall back for `null`/`undefined`, not falsy-but-present
values like `""`). No translation step was needed anywhere downstream -
`new_entrants`, `merge_into_cache`, `write_parquet`, and
`join_common_names` all already handled a `None` value correctly once
it existed, confirmed by tests rather than assumed.

The existing production cache (59,199 rows, all hits, from before this
fix) was backfilled in place via a one-off script
(`jobs/backfill_common_names_negative_cache.py`) rather than letting the
next run re-discover the gap through one more real 47k-call fetch: it
recomputes the top-N set locally, marks whatever's missing from the
current cache as confirmed-empty, and uploads - no ChecklistBank calls
at all, trusting the 24 Oct run's already-completed result (no
exception was logged during it, and each per-taxon fetch call raises on
any HTTP error, so a clean run means every one of those lookups
genuinely returned empty, not that it silently failed).

**Validated live** (25 Oct 2026, after deploying the fix and running the
backfill): the next real run showed only 35 new entrants (4 real names,
31 confirmed-empty) instead of 47,070+ - both outcomes correctly
persisted this time. Step 4 dropped from ~8m45s to ~17s; total job
runtime from ~11m50s to ~3min. The 35 aren't new data - comparing the
two fact files directly, every swapped-in/swapped-out specieskey at the
boundary had the *identical* occurrence total (e.g. 303) in both -
`compute_top_n_specieskeys`'s `ORDER BY total_occ DESC` has no secondary
tiebreaker, so which of several species tied at the exact cutoff land
just inside vs. just outside the top-N is arbitrary per execution. A
small number of "new entrants" per run from this tie-breaking noise is
expected going forward, not a sign of a problem - a sharp contrast with
the fixed 47k/run bug.

## Alternatives Considered

### A separate "attempted" marker/column instead of `common_name = None`
- Pros: makes the three states (never tried / tried-empty / tried-hit)
  explicit in the schema rather than relying on NULL's dual meaning.
- Rejected because: `None` already is the correct value everywhere
  downstream (dimension file, frontend) - adding a second column would
  mean translating between two NULL-shaped-but-different things at the
  join step for no actual gain.

### Per-task fault isolation in `fetch_common_names_for_taxa`
`future.result()` isn't wrapped per-task - a single HTTP failure among
potentially thousands of real calls still propagates out and discards
the whole batch's results (hits and confirmed-empties alike) via
`fetch_and_merge_common_names`'s outer `except Exception`.
- **Explicitly deferred, not fixed here.** Reasoning (the user's): the
  existing cache is kept unchanged on any such failure, and that cache
  already provides significant, durable coverage - a transient failure
  costs one skipped run's worth of (mostly negative) results, not
  correctness. Worth revisiting if it ever actually recurs in
  production, not worth building for pre-emptively.

## Consequences

- The cache grows from "however many species have a name" (59,199) to
  "however many species have ever been in the top-N" (~106,247,
  bounded by the same 99%-coverage definition that already exists) -
  a one-time, bounded growth, not unbounded.
- **A `None` entry is permanent and never re-checked.** If
  ChecklistBank's vernacular-name coverage for one of these ~47,000
  species is ever updated in the future, this pipeline will never
  notice, because the specieskey is already "present" in the cache.
  Accepted as a reasonable trade-off (ChecklistBank's coverage for
  already-long-tail species is unlikely to change often) - a genuine
  future improvement would need a manual one-off re-check or a
  TTL/retry-after-N-months policy, neither built now.
- **A small amount of per-run churn at the top-N boundary is now
  expected and normal**, not a bug: ties at the exact 99%-coverage
  cutoff mean a handful of specieskeys swap in/out of the top-N each
  run with no underlying data change, each costing one real
  ChecklistBank lookup the first time. This is a minor, accepted cost,
  not worth adding a deterministic tiebreaker (e.g. `, specieskey`) to
  `compute_top_n_specieskeys`'s `ORDER BY` to eliminate - noted here so
  a future reader doesn't mistake this churn for a regression.
- **New, previously unflagged risk worth a future reader's attention:**
  cache entries (hits and confirmed-empties alike) aren't versioned
  against which `CHECKLISTBANK_COL_XR_DATASET_KEY` they were fetched
  under. That constant's own comment already says updating it is a
  deliberate, manual, never-automatic decision (a new COL XR release) -
  but nothing today ties a cached result to the dataset-key version
  that produced it. If that constant is ever updated, every existing
  cache entry silently carries forward from the old release without
  re-checking, even though a new COL XR release could plausibly have
  different vernacular-name coverage entirely. Whoever next updates
  that constant should clear (or explicitly re-derive)
  `common_names_cache.parquet` as part of that change, not assume the
  old cache still applies.
