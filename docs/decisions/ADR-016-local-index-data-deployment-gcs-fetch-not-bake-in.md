# ADR-016: Local Index Data (Fact/Dimension Parquet) Served from GCS, Not Baked into the Docker Image

## Status
Accepted

## Date
2026-10-20

## Context

This session's prior work (`prototypes/duckdb_e2e_prototype.py`, `prototypes/scripts/species_common_names_batch.py`) validated a local DuckDB/Parquet index — a fact table (`fact_sorted_by_cell.parquet`, ~710MB) and a dimension table (`species_dimension_with_common_names.parquet`, ~40MB) — as a replacement for the live GBIF `species/match`/`occurrence/search`/`vernacularNames` calls the app currently makes per request. This removes GBIF as a live-request-path dependency, leaving a local index that still needs to reach production Cloud Run (see ADR-001).

Two ways to get the data onto the running service were considered: bake it into the Docker image at build time, or have the app query it live from a Google Cloud Storage bucket via DuckDB's `httpfs` support.

Querying GCS directly was not yet measured in this project — the comparison below combines the real, measured local-disk baseline (`duckdb_parquet_prototype.py`'s `EXPLAIN ANALYZE` runs: 0.447-0.795s depending on row-group contiguity) with a reasoned, unvalidated estimate for the GCS/`httpfs` path (roughly +50-350ms per request from additional HTTP range requests, same-region). That estimate should be confirmed with a real test before this is built.

## Decision

Serve the fact and dimension Parquet files from a **GCS bucket**, queried live by DuckDB's `httpfs` support, rather than baking them into the Docker image.

## Alternatives Considered

### Bake into the Docker image (`COPY` at build time)
- Pros: lower query latency (data already on local disk, no network round trip per query); simplest attack surface (no new live runtime dependency or bucket IAM to manage — risk is confined to the existing build/deploy pipeline, which GCP's tooling, e.g. Binary Authorization, already covers); free historical versioning and rollback (each Cloud Run revision is an immutable code+data snapshot; a bad data refresh just means the previous revision keeps serving, no explicit rollback action needed).
- Cons: couples the data-refresh pipeline to the code build/deploy pipeline — a routine code-only fix either has to re-run the data pipeline too (slower, and newly failure-coupled to GBIF/ChecklistBank availability) or needs explicit special-casing to reuse previously-baked data; image size scales with data size, so build/push/cold-start time all degrade if this dataset grows substantially (does not affect Cloud Run's horizontal request-serving scalability, only per-instance startup and deploy cost); making the full pipeline (data refresh → image build → push → deploy) unattended requires building a complete CI/CD chain (Cloud Build trigger, smoke-test-gated traffic shift) that doesn't exist yet.
- Rejected because: the coupling and growth-scaling costs are real and compound over time, while GCS-fetch's latency/cost downsides are small and bounded at this project's current scale.

## Consequences

- **Latency**: estimated +50-350ms per query versus the baked-in baseline (0.6-1.0s typical vs. 0.45-0.8s measured locally; possibly up to ~1.3-1.5s on a cold instance's first query). This is an estimate, not a measurement — should be validated with a real `httpfs` test against a GCS bucket before/shortly after this is built. Either way, both are well under the live-GBIF-API latency this architecture replaces.
- **Cost**: negligible either way at current scale (~1.5¢/month GCS storage for ~750MB; same-region reads are free egress with sub-cent Class B operation costs even at meaningful query volume). Not the deciding factor.
- **New security/operational surface**: this introduces a live, independently-addressable runtime dependency (the bucket) that the baked-in approach doesn't have. Requires:
  - Two separate service accounts: the live app gets read-only access (`roles/storage.objectViewer`) scoped to this one bucket; the monthly data-refresh job gets a separate identity with write access. The app's own identity must never have write access to the bucket it reads from.
  - The bucket must never be publicly writable (a common real-world GCS misconfiguration class) — write access determines data integrity for every user query, not just confidentiality (the data itself is already public GBIF-derived data, so confidentiality is a non-issue; integrity is the actual risk).
  - Worth pinning to a specific GCS object generation (rather than always "latest") and/or verifying checksums client-side, so a bad write doesn't silently propagate to every live query until explicitly rolled forward.
  - The live app now has an availability dependency on GCS being reachable — a GCS-side issue becomes a live production failure, not just a failed deploy.
- **Decoupling benefit**: code deploys and data refreshes become independent processes with independent failure domains and release cadences — a ChecklistBank/BigQuery hiccup during a data refresh no longer has any bearing on shipping an unrelated code fix, and vice versa.
- **Follow-on work**: the monthly automated data-refresh pipeline (BigQuery rescan, dimension rebuild, common-name lookup, write to the bucket) is designed but not yet built (see `docs/status_docs/` session notes, 2026-10-20). For the first production deployment, this ADR's immediate scope is a one-time, manual upload of the current Parquet files to the bucket — the automated monthly refresh is explicitly deferred to a later session.
