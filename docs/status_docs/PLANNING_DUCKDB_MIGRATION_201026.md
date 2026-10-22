# Planning: DuckDB/GCS Migration — 20 October 2026

High-level reference plan for migrating the app off live GBIF calls onto the
local DuckDB/Parquet index (`fact_sorted_by_cell.parquet`,
`species_dimension_with_common_names.parquet`), served from GCS per
ADR-016. Not an exact spec — expect deviation as implementation surfaces
real constraints. Revisit and amend in place rather than superseding with a
new dated file, unless the plan changes substantially enough to warrant a
fresh snapshot.

## Phase 0 — Baseline & regression harness

1. Capture fixed e2e tests against the *current live GBIF path* for a small
   set of fixed areas (Retiro first, reusing its existing polygon) —
   latency, species returned, counts.
2. Freeze matching expected-result fixtures against the new DuckDB/Parquet
   index for the same areas/month, since GBIF's snapshot is now static data
   we control — diffable species-set/rank assertions, not just latency
   comparison.
3. Set tolerance bands (exact species-set match for a fixed month; latency
   target, e.g. stay under live-GBIF baseline).

## Phase 1 — De-risk the GCS deployment route

1. Stand up bucket + IAM (read-only app SA / write-only refresh SA, per
   ADR-016) via Terraform.
2. One-time manual upload of the two Parquet files.
3. Run the real `httpfs` timing harness against the `gs://` path — replace
   ADR-016's estimated +50–350ms with a measured number.
4. Checkpoint: confirm GCS-fetch still beats bake-in before any app code
   depends on it.

## Phase 2 — Wire the app to the new index (backend)

1. Collapse `taxon_resolution.resolve_taxon_key` (GBIF `species/match`) +
   `gbif_client.fetch_top_species` (GBIF `occurrence/search`, pagination,
   clustering) into **one DuckDB query per taxon filter**, joining fact +
   dimension (+ common names) parquet, filtered on the taxon-rank column,
   ranked by occurrence count. Productionize the Testudines-style rank
   override; resolve the outstanding `TAXON_GUIDANCE` string-rank spot-check
   against local string columns.
2. Keep `_select_species_across_groups` and `order_waypoints` unchanged —
   verify the DuckDB-produced `list[list[dict]]` satisfies their existing
   contract.
3. Drop `species_enrichment.fetch_common_name`'s live GBIF call —
   `common_name` comes pre-joined from
   `species_dimension_with_common_names.parquet` on the same `specieskey`;
   `_enrich_one` keeps only the Wikipedia fetch. Implement the
   NULL→scientific-name fallback in code.
4. Delete now-dead code: `GbifUnavailableError`/the `gbif_unavailable`
   branch in `query.py`, `GBIF_FETCH_DEADLINE_SECONDS`, the
   scale-guard/year-fallback probe, `_cluster_species_hotspot`, and both
   `ThreadPoolExecutor`s (pending Phase 0 confirming sequential local
   queries are fast enough).
5. Audit `species_key` usages for the GBIF-numeric → COL-XR-string type
   change.

## Phase 2 — Wire the app to the new index (frontend)

1. Add cell-footprint rendering in `MapView.tsx` using the new
   `cell`/`cell_lat_err`/`cell_lon_err` fields (the prototype's zone-not-pin
   UX) alongside/replacing the existing `hotspot_lat`/`hotspot_lon` point
   marker — sanity-check the visual treatment via `/prototype` or
   `/agent-browser` before committing.
2. Update `MapView.test.tsx`, `ResultsPanel.test.tsx`,
   `NarrationControl.test.tsx` fixtures to the new response shape (cell
   fields present, `species_key` now a string, `common_name` possibly
   null).
3. Decide and implement the null-common-name UI treatment.

## Phase 2 gate

Run Phase 0's regression harness against the new wiring end-to-end,
including the frontend change, before calling the migration done.

## Phase 3 — Operational hardening

1. Checksum/generation-pinning on bucket objects.
2. Staging smoke test before prod cutover.

## Phase 4 — Deferred

1. Monthly automated refresh pipeline.
2. Carryover: polygon-geometry beyond bbox, backlog reconciliation with
   `docs/FEATURE_IDEAS_BACKLOG.md`.
