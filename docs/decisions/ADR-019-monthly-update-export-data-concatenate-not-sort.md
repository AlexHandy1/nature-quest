# ADR-019: Monthly GBIF Update Uses EXPORT DATA + Ordered Concatenation, Not extract_table() + Local Sort

## Status
Accepted

## Date
2026-10-24

## Context

The monthly GBIF data-update pipeline (`app/backend/jobs/monthly_gbif_update.py`,
a Cloud Run Job on a Cloud Scheduler trigger — automates the manual
8-9 Oct table-creation process) derives a fact table (~221M rows, grouped
by cell+specieskey) and a dimension table from a session-scoped BigQuery
TEMP TABLE, then needs both exported to GCS as the production-serving
Parquet files. The fact table must be ordered by `cell` — the live app's
DuckDB queries rely on Parquet row-group pruning over that sort order for
acceptable latency.

BigQuery's single-file export limit is ~1GB logical size, so the fact
table (~750MB-800MB in production, growing over time) is already close to
needing sharding, and will need it reliably as GBIF's data grows. A
sharded export requires a wildcard destination URI and produces a
non-deterministic number of files.

The first implementation used `client.extract_table()` on an intermediate
BigQuery destination table (the query's own `ORDER BY cell` first wrote to
a plain table, then a separate extract job copied that table to GCS).
This gives **no row-order guarantee at all**: a BigQuery table has no
order at rest regardless of a prior query's `ORDER BY`, and
`extract_table()` itself makes no ordering promise either. The pipeline
therefore had to re-sort the entire fact table locally after download,
via `DuckDB`'s `COPY ... ORDER BY cell`.

This full local re-sort OOM-killed a real Cloud Run Job execution even
at 16Gi/4 vCPU (up from an initial 4Gi, also OOM-killed) — DuckDB's sort
peaked at ~8.4GB RSS / ~13-14GB peak memory footprint locally, and Cloud
Run's **default container filesystem is memory-backed** (confirmed via
Google's own docs — writes draw on instance memory, with no separate
disk allocation unless the Preview "ephemeral disk" feature is
explicitly configured), so the downloaded shard bytes and the recombined
output file's bytes add directly on top of that compute peak in the same
memory pool. The real production shard set (~2.08GB) pushed estimated
total well past 16Gi.

## Decision

Replace `extract_table()` on an intermediate destination table with a
direct `EXPORT DATA OPTIONS(uri=..., format='PARQUET', overwrite=true)
AS <query>` statement, run within the same BigQuery session as the
`agg` TEMP TABLE scan. Per BigQuery's own documentation
(https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/export-statements):

> "When you export to multiple files, the ordered sequence is preserved
> globally across all generated files" — and, with an `ORDER BY` clause,
> "the globally sorted result set is distributed sequentially" across
> shards.

This guarantee lets the local recombine step become a plain **ordered
concatenation** instead of a re-sort: download the shards (GCS
guarantees lexicographic blob listing order, matching BigQuery's
zero-padded shard names), concatenate them via DuckDB's
`read_parquet([ordered_list])` in that exact order (confirmed via a
dedicated test that the list order is honoured over filename order —
not something to assume), and write the result with
`ROW_GROUP_SIZE` pinned to match the existing production convention.

Two verification checks guard this, both treated as hard mechanism
failures (crash the job, trip the GCP-level backstop alert) rather than
soft guardrail outcomes, since they'd indicate something broke in our
own export/recombine mechanism, not a data-quality signal:
- **Row-count cross-check**: sum of shard rows must equal the recombined
  file's row count.
- **Row-group monotonicity check**: queries `parquet_metadata()` directly
  and asserts each row group's `cell` range doesn't overlap/precede the
  previous one. This is the check that actually *proves* BigQuery's
  ordering guarantee held for the real output, rather than trusting the
  documentation blindly — confirmed to have real teeth by running it
  against genuinely unordered (old-method) real shards and watching it
  correctly raise.

`EXPORT DATA`'s `uri` option turned out to **always** require a wildcard
character, with no single-file exception for small exports (unlike
`extract_table()`) — confirmed via BigQuery's docs and a free literal-query
test after a real run failed with `Invalid uri specification`. The
dimension table (which has no ordering requirement — it's a
`SELECT DISTINCT`, no `ORDER BY`) therefore also exports via a wildcard
shard prefix and the same ordered-concatenation + row-count-check path,
just without the sortedness check.

Measured locally against the real 221M-row production dataset: peak
memory for the concatenation approach is **~2.75GB**, versus **~14GB**
for the full sort — and it runs in ~15s versus ~77s. A full end-to-end
local harness (fake GCS backed by a scratch directory, real
production-scale cached data, only the BigQuery call itself mocked)
measured the *entire* pipeline's peak at ~4GB, with an estimated
~7.8GB Cloud-Run-equivalent total once the memory-backed-filesystem
file-bytes are added back — comfortable margin under the 16Gi limit
that was raised to recover from the original OOM.

## Alternatives Considered

### Keep extract_table() + local full sort, just raise Cloud Run memory further
- Pros: smaller code change; no new assumption about `EXPORT DATA`'s
  behaviour to validate.
- Cons: the underlying problem (file I/O and compute share one memory
  pool on Cloud Run) doesn't go away — the number needed will keep
  creeping up as GBIF's dataset grows month over month, the same
  structural fragility that ruled out a pandas-single-file alternative
  earlier in this design process.
- Rejected because: it treats the symptom, not the cause, and the real
  fix (EXPORT DATA's ordering guarantee) was available and nearly free
  to validate.

### pandas single-file conversion + a large (20GB+) Cloud Run instance
- Pros: produces one file directly, no sharding/recombine step at all.
- Cons: measured locally at 14.93-24.9GB peak memory for the *current*
  data size alone, with little headroom as GBIF's data grows; Cloud
  Run's hard ceiling is 32GiB per instance.
- Rejected because: same structural fragility as above, with less
  headroom to begin with.

### Dataform for the transformation step
- Pros: GCP-native, free, BigQuery-integrated, built-in assertions.
- Cons: doesn't touch the actual problem at all — the export/shard/
  recombine/promote mechanism sits entirely outside Dataform's scope
  regardless of which tool runs the SQL transformations.
- Rejected because: it would add a new tool without solving anything.

## Consequences

- The pipeline no longer creates intermediate BigQuery destination
  tables for either table — `generate_target_release` only derives and
  `EXPORT DATA`s directly to GCS.
- Guardrail stats for the dimension table are computed from the
  *pre-common-names-join* local file, not the final joined one — this
  is deliberately fine, not a gap: a `LEFT JOIN` can never change row
  count or the specieskey set, so pre- and post-join dimension stats are
  mathematically identical on that front, and the schema check's
  `common_name`-present expectation is asserted synthetically rather
  than re-derived, since it's guaranteed by `join_common_names`'s own
  structure, not by anything data-dependent.
- A real, confirmed operational gotcha (not yet automated around, a
  deliberate scope decision): `EXPORT DATA`'s `overwrite=true` replaces
  files it rewrites but does **not** delete files under the wildcard
  that a smaller/different run no longer produces — confirmed directly
  by writing a stray file and watching it survive a fresh export. For a
  once-a-month job, manually clearing the dated release prefix before a
  same-day re-run (as happened repeatedly during this session's
  validation) is the chosen mitigation over adding a delete-before-export
  step to the code.
- The Cloud Run Job's memory was raised from 4Gi/2 vCPU to 16Gi/4 vCPU
  as part of recovering from the original OOM. With the sort removed,
  there's now real headroom to size this back down in the future if
  desired, though the cost difference is negligible for a job that runs
  once a month either way.
