# ADR-017: Local DuckDB/Parquet Index Replaces Live GBIF API Calls

## Status
Accepted

## Date
2026-10-21

## Context

`/api/query` previously resolved each taxon filter against GBIF's live APIs
on every single request: `species/match` to turn a `{taxonRank, taxonValue}`
pair into a numeric backbone key (`services/taxon_resolution.py`), then
`occurrence/search` to fetch and rank matching occurrences within the
search polygon (`services/gbif_client.py`). This was a real production
reliability problem, not a theoretical one: GBIF rate-limiting under larger
search areas caused repeated failures (see `WORK_SUMMARY_150826.md`'s
50km²→25km² area-cap revert), and `GbifUnavailableError`/`gbif_unavailable`
existed specifically to give users a clear message when GBIF itself was
slow or failing — a dependency entirely outside this app's control.

Earlier sessions (`WORK_SUMMARY_081026.md` onward, prototyped in
`prototypes/scripts/duckdb_*.py`) validated an alternative: export GBIF's
public BigQuery occurrence data into a `cell`-sorted Parquet fact table
(`fact_sorted_by_cell.parquet`, aggregated per `(geohash cell, species)`
pair) and a species dimension table, then query them locally via DuckDB
with row-group pruning instead of calling GBIF's live API per request. This
ADR covers porting that design to production.

## Decision

Replace the live GBIF taxon-resolution and occurrence-search calls with a
single local DuckDB query per taxon filter (`services/species_index.py`,
`query_species_for_filter`), against Parquet files served from GCS (see
ADR-016) or local files in development:

1. **Taxon resolution becomes a local string match, not a GBIF API call.**
   `services/taxon_resolution.py` (GBIF `species/match`) is deleted
   entirely. The LLM's `{taxonRank, taxonValue}` output is matched directly
   against the local dimension table's string columns
   (`LOWER(column) = LOWER(taxonValue)`) — no numeric-key indirection at
   all. `TAXON_GUIDANCE` (now `services/prompts/taxon_resolution.py`) was
   corrected for this: Testudines is `order`-rank against this local table
   (verified directly against the data), where it was `class`-rank
   specifically to work around GBIF's backbone having no single `Reptilia`
   class — that workaround no longer applies.
2. **Occurrence ranking, hotspot location, and common-name lookup collapse
   into one query.** `query_species_for_filter` filters by taxon, sums
   occurrence counts per species across the candidate cells, picks each
   species' single highest-count cell as its "hotspot," and joins
   `common_name` from the same dimension table — all in one DuckDB
   statement, replacing `gbif_client.fetch_top_species` (live
   `occurrence/search`) and `species_enrichment`'s live GBIF
   `vernacularNames` call.
3. **A species' hotspot is now a geohash cell, not a density-cluster
   point.** The aggregated fact table only has `(cell, specieskey,
   occurrence_count)`, not individual occurrence coordinates, so the
   per-occurrence density-clustering algorithm (`_cluster_species_hotspot`)
   has no equivalent and is deleted. `hotspot_lat`/`hotspot_lon` is now a
   decoded cell centroid; `cell`/`cell_lat_err`/`cell_lon_err` (the cell's
   real footprint) are new fields the frontend uses to render a zone, not
   a precise point (`MapView.tsx`) — several species can now genuinely
   share the same top cell, which the old per-occurrence clustering rarely
   produced.
4. **The "unresolved taxon" vs. "no occurrences here" distinction is
   intentionally dropped.** GBIF's `species/match` gave a clean signal for
   "this taxon name doesn't exist" (surfaced via `unresolvedGroups`),
   separate from "it exists but nothing's here" (`no_results`). The local
   query has no equivalent middle step — both now produce an empty group,
   surfacing as `no_results`. Accepted because `TAXON_GUIDANCE` already
   constrains the LLM to taxonomic names it's confident about, so this
   rarely triggers in practice.
5. **The polygon area cap changes meaning.** The old 25km² cap
   (`models/query.py`, `MAX_POLYGON_AREA_KM2`) existed specifically to
   bound GBIF rate-limit exposure. It was removed, then reinstated at a
   much larger threshold sized for this architecture's own cost driver
   instead — `services/species_index.py`'s `candidate_cells` grid-samples
   the polygon's bounding box in Python, with a cost that scales with area
   regardless of GBIF. See the constant and its surrounding comment in
   `models/query.py` for the current value and reasoning; not restated
   here since it's a live guardrail value, not historical context.

## Alternatives Considered

### Keep live GBIF calls, fix reliability with more retries/backoff
- Pros: no new data pipeline, no local index to keep fresh.
- Cons: doesn't address the root cause (GBIF rate-limiting scales with
  search area and query volume, both of which are expected to grow); every
  request stays latency-bound by GBIF's own response times.
- Rejected because: this was already tried in spirit (the area-cap
  revert, the fetch-deadline/retry logic in the now-deleted
  `gbif_client.py`) and only ever treated the symptom.

### Curated lookup cache for common taxa, GBIF fallback for the rest
- Pros: removes GBIF from the hot path for the most common queries without
  a full data pipeline.
- Cons: partial fix — any uncached taxon or large area still hits GBIF live;
  adds a second system to keep in sync with the full local-index approach
  if that's ever built anyway.
- Rejected because: the full local index (already prototyped and proven
  fast via row-group pruning) removes GBIF from the hot path entirely, for
  the same or less engineering effort as a partial cache.

## Consequences

- **GBIF is no longer a live dependency of the request path at all.**
  `GbifUnavailableError`, the `gbif_unavailable` outcome, and the frontend's
  GBIF-specific status-link UI are all deleted — there's no code path left
  that can produce that outcome.
- **A new live dependency is introduced instead: the GCS bucket serving
  the Parquet files** (see ADR-016 and ADR-018 for that design and its own
  consequences).
- **Data freshness is now a deliberate, separate concern.** The local
  index is a point-in-time snapshot (a one-time manual upload as of this
  ADR), not live GBIF data. The monthly automated refresh pipeline that
  would keep it current is explicitly deferred to a later session — see
  `docs/status_docs/PLANNING_DUCKDB_MIGRATION_201026.md`.
- **`species_key` changes meaning and is no longer GBIF's legacy numeric
  backbone key** — it's now a COL XR string ID from the local dimension
  table. The frontend's GBIF species-page link
  (`gbif.org/species/{species_key}`), which only resolves on the legacy
  numeric key, was dropped rather than silently broken.
- **`ADR-011`'s multi-taxon quota/merge design (`_select_species_across_groups`)
  is unchanged and reused as-is** — only the per-filter resolution and
  fetch steps it feeds from were replaced. That ADR's GBIF-key-resolution-
  specific decisions and consequences (its point 3, and the "no GBIF
  call-volume guardrail" consequence) are superseded by this one.
