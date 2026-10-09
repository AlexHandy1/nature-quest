#!/usr/bin/env python3
"""
PROTOTYPE — duckdb_e2e_prototype.py

Question: does the full request path — NL query -> LLM taxon resolution ->
polygon -> candidate geohash cells -> DuckDB query against the cached
Parquet fact/dimension tables -> species selection/ordering -> front-end
map — work end-to-end for Retiro Park, using the local-index design
validated in duckdb_parquet_prototype.py (9 Oct session)?

Deliberately out of scope (next session's work, see WORK_SUMMARY_091026.md
next steps): the GBIF vernacularNames common-names batch job. Species are
shown by scientific name only here — carry this gap into next session's
next-steps as an important item to close before any production build.

Reused, unmodified, from the real production backend (app/backend/services):
  - anthropic_client.build_client / resolve_taxon_filters / TAXON_GUIDANCE —
    the LLM step's output ({taxonRank, taxonValue} pairs) is already shaped
    for this prototype's needs; the GBIF-specific key-resolution step that
    normally follows it (taxon_resolution.py's resolve_taxon_key(), which
    calls GBIF's species/match to turn a rank+name into a numeric backbone
    key) is DROPPED entirely, not adjusted — the local Parquet dimension
    table already carries kingdom/phylum/class/order/family/genus as plain
    strings, so filtering goes straight from the LLM's output to a SQL
    string-equality WHERE clause (per the 8 Oct session's decision).
  - gbif_client._select_species_across_groups — quota/round-robin selection
    across resolved taxon groups, unchanged. Handles any N-way mixed-taxa
    split (e.g. "plants and birds" -> 2 groups; "reptiles" -> 4; "fish" -> 7)
    without any prototype-side special-casing.
  - waypoints.order_waypoints — nearest-neighbour route ordering, unchanged.

ONE real adjustment was needed, not assumed — verified live against the
cached dimension Parquet before writing this:
    SELECT DISTINCT class, "order" FROM species_dimension WHERE ... testud%
    -> [('Reptilia', 'Testudines')]
Production's TAXON_GUIDANCE forces taxonRank="class" for "Testudines"
specifically because GBIF's backbone API has no single "Reptilia" class.
Our local table (sourced from BigQuery's raw occurrence string columns, not
GBIF's backbone API) is the opposite: class='Reptilia' is a real value here,
and 'Testudines' only appears in the order column. Querying `class =
'Testudines'` against this table returns zero rows every time. See
adapt_filter_for_local_table() below for the narrow, evidenced fix — not a
rewrite of the prompt, just a rank override for this one proven case.
Everything else from TAXON_GUIDANCE (reptile/fish expansion, negation
handling, multi-taxa segmentation) is still unverified against the local
string columns — same still-open spot-check carried over twice in
WORK_SUMMARY_081026.md / WORK_SUMMARY_091026.md next-steps, not resolved by
this prototype.

Candidate-cell generation (bbox grid-sampling) is copied from
duckdb_parquet_prototype.py per this codebase's "prototypes stay standalone"
convention, not imported.

No raw occurrence points exist in the aggregated fact table (cell,
specieskey, occurrence_count only) — each species' hotspot is the centroid
of whichever candidate cell holds its single highest occurrence_count,
decoded via pygeohash. This is a known simplification vs. production's
per-occurrence density clustering (gbif_client._cluster_species_hotspot).

Per-filter DuckDB queries run sequentially, not in a thread pool like
production's per-filter GBIF calls — each query only touches ~1 row group
per the 9 Oct row-group-pruning validation (sub-second), so even a 7-filter
"fish" query should stay well under a few seconds total.

Requires ANTHROPIC_API_KEY in the environment, and the cached Parquet files
from duckdb_parquet_prototype.py (prototypes/scratch/*.parquet — run that
script first with --export if they don't exist yet).

Run standalone (CLI, for quick sanity checks outside the server):
  source venv/bin/activate && python prototypes/scripts/duckdb_e2e_prototype.py "birds" \\
    2>&1 | tee prototypes/logs/duckdb_e2e_$(date +%Y%m%d_%H%M%S).log
"""

import os
import sys
import time

import duckdb
import pygeohash as pgh
from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app", "backend")
sys.path.insert(0, os.path.abspath(BACKEND_DIR))

from services.anthropic_client import build_client, resolve_taxon_filters  # noqa: E402
from services.gbif_client import _select_species_across_groups  # noqa: E402
from services.waypoints import order_waypoints  # noqa: E402

FACT_PARQUET = "prototypes/scratch/fact_sorted_by_cell.parquet"
DIM_PARQUET = "prototypes/scratch/species_dimension.parquet"

# Same fixed Retiro Park polygon used throughout this codebase's prototypes
# (duckdb_parquet_prototype.py, services/gbif_client.py's GBIF_POLYGON).
RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)
GEOHASH_PRECISION = 6
TOP_SPECIES_COUNT = 5

RANK_TO_COLUMN = {
    "kingdom": "kingdom",
    "phylum": "phylum",
    "class": "class",
    "order": "order",
    "family": "family",
    "genus": "genus",
}

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"


def polygon_bbox(wkt_polygon: str) -> tuple[float, float, float, float]:
    coords_str = wkt_polygon.split("((")[1].split("))")[0]
    points = [tuple(map(float, pair.split())) for pair in coords_str.split(",")]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), max(lats), min(lons), max(lons)


def polygon_centroid(wkt_polygon: str) -> tuple[float, float]:
    coords_str = wkt_polygon.split("((")[1].split("))")[0]
    points = [tuple(map(float, pair.split())) for pair in coords_str.split(",")]
    lats = [p[1] for p in points]
    lons = [p[0] for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def candidate_cells(wkt_polygon: str, precision: int = GEOHASH_PRECISION, step_deg: float = 0.001) -> list[str]:
    min_lat, max_lat, min_lon, max_lon = polygon_bbox(wkt_polygon)
    cells = set()
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            cells.add(pgh.encode(lat, lon, precision=precision))
            lon += step_deg
        lat += step_deg
    return sorted(cells)


def adapt_filter_for_local_table(taxon_rank: str, taxon_value: str) -> tuple[str, str]:
    """Translates one LLM-produced {taxonRank, taxonValue} pair into the rank
    actually used by the local dimension table's string columns. Narrow,
    evidenced override — see this module's docstring for the verified
    class='Reptilia'/order='Testudines' finding. Everything else passes
    through unchanged (still unverified against this table, not assumed
    correct — see docstring)."""
    if taxon_value.strip().lower() == "testudines":
        return "order", taxon_value
    return taxon_rank, taxon_value


def resolve_taxon_filters_for_query(query: str) -> list[dict]:
    """LLM step, reused unmodified from production (anthropic_client.py) —
    its {taxonRank, taxonValue} output is already the shape this prototype's
    DuckDB filtering needs. No GBIF species/match call follows it here."""
    client = build_client()
    return resolve_taxon_filters(query, client)


def query_species_for_filter(con: duckdb.DuckDBPyConnection, taxon_filter: dict, cells: list[str]) -> list[dict]:
    taxon_rank, taxon_value = adapt_filter_for_local_table(
        taxon_filter["taxonRank"], taxon_filter["taxonValue"]
    )
    column = RANK_TO_COLUMN[taxon_rank]
    cell_list = ", ".join(f"'{c}'" for c in cells)

    sql = f"""
        WITH joined AS (
            SELECT f.cell, f.occurrence_count, s.species, s.specieskey, s.kingdom
            FROM read_parquet(?) f
            JOIN read_parquet(?) s ON f.specieskey = s.specieskey
            WHERE f.cell IN ({cell_list}) AND LOWER(s."{column}") = LOWER(?)
        ),
        totals AS (
            SELECT species, specieskey, kingdom, SUM(occurrence_count) AS total
            FROM joined GROUP BY species, specieskey, kingdom
        ),
        ranked_cells AS (
            SELECT species, cell, occurrence_count,
                   ROW_NUMBER() OVER (PARTITION BY species ORDER BY occurrence_count DESC) AS rn
            FROM joined
        )
        SELECT t.species, t.specieskey, t.kingdom, t.total, r.cell AS hotspot_cell
        FROM totals t JOIN ranked_cells r ON t.species = r.species AND r.rn = 1
        ORDER BY t.total DESC
        LIMIT 50
    """
    rows = con.execute(sql, [FACT_PARQUET, DIM_PARQUET, taxon_value]).fetchall()

    species_list = []
    for species, specieskey, kingdom, total, hotspot_cell in rows:
        lat, lon, lat_err, lon_err = pgh.decode_exactly(hotspot_cell)
        species_list.append(
            {
                "species": species,
                # The geohash cell's actual footprint (centroid +/- error
                # margin), not just its centroid — lets the frontend draw
                # the real ~cell-sized zone a species was found in, rather
                # than implying point-precision the aggregated fact table
                # doesn't have. See this session's UX discussion: markers
                # were clustering indistinguishably at shared cell centroids.
                "cell": hotspot_cell,
                "cell_lat_err": lat_err,
                "cell_lon_err": lon_err,
                # NOT a legacy GBIF numeric speciesKey, by design of GBIF's
                # own backbone migration — verified live against BigQuery
                # during this session: Pica pica (legacy GBIF key 2482484)
                # comes back as specieskey='4HPXM' here. GBIF migrated its
                # taxonomic backbone to Catalogue of Life (COL XR), whose
                # taxon IDs are short alphanumeric strings like this one
                # (https://data-blog.gbif.org/post/catalogue-of-life-taxonomic-backbone/);
                # the public BigQuery occurrences dataset's specieskey column
                # now carries the COL XR id. Old numeric keys still work on
                # GBIF's live APIs but only with the legacy backbone's
                # checklistKey explicitly supplied — the default has moved.
                # Kept as an opaque string here (never int-cast, never used
                # to build a gbif.org/species/{id} link): the common-names
                # batch job (next-steps) will need a COL XR -> legacy numeric
                # key mapping step first (GBIF publishes one at
                # download.checklistbank.org/col/gbif/README.html), not just
                # a straight vernacularNames call.
                "species_key": specieskey,
                "count": int(total),
                "kingdom": kingdom or "?",
                "hotspot_lat": lat,
                "hotspot_lon": lon,
            }
        )
    return species_list


def run_query(user_query: str) -> dict:
    """Returns {"status": "resolved"|"unresolved"|"no_results",
    "taxonFilters": [...], "species": [...ordered...], "message": str,
    "timings_ms": {"llm": float, "duckdb_total": float, "duckdb_per_filter":
    [float, ...], "pipeline_total": float}}."""
    pipeline_start = time.perf_counter()

    print(f"\n{BOLD}--- STEP 1: LLM taxon resolution ---{RESET}")
    llm_start = time.perf_counter()
    taxon_filters = resolve_taxon_filters_for_query(user_query)
    llm_elapsed_s = time.perf_counter() - llm_start
    print(f"  {taxon_filters} {DIM}[{llm_elapsed_s * 1000:.0f}ms]{RESET}")
    if not taxon_filters:
        pipeline_elapsed_s = time.perf_counter() - pipeline_start
        print(f"\n{BOLD}Pipeline total: {pipeline_elapsed_s * 1000:.0f}ms (unresolved — no DuckDB queries run){RESET}")
        return {
            "status": "unresolved",
            "taxonFilters": [],
            "species": [],
            "message": "Couldn't match that to a category we support yet — try something like 'birds' or 'plants'.",
            "timings_ms": {
                "llm": llm_elapsed_s * 1000,
                "duckdb_total": 0.0,
                "duckdb_per_filter": [],
                "pipeline_total": pipeline_elapsed_s * 1000,
            },
        }

    print(f"\n{BOLD}--- STEP 2: candidate cells (Retiro, precision {GEOHASH_PRECISION}) ---{RESET}")
    cells = candidate_cells(RETIRO_POLYGON)
    print(f"  {len(cells)} candidate cells")

    print(f"\n{BOLD}--- STEP 3: DuckDB query per taxon group (sequential) ---{RESET}")
    con = duckdb.connect()
    groups = []
    duckdb_query_times_ms = []
    for tf in taxon_filters:
        query_start = time.perf_counter()
        species_list = query_species_for_filter(con, tf, cells)
        query_elapsed_s = time.perf_counter() - query_start
        duckdb_query_times_ms.append(query_elapsed_s * 1000)
        print(
            f"  {tf['taxonRank']}/{tf['taxonValue']:<20} -> {len(species_list):>3} species "
            f"{DIM}[{query_elapsed_s * 1000:.0f}ms]{RESET}"
        )
        groups.append(species_list)
    duckdb_total_ms = sum(duckdb_query_times_ms)
    print(
        f"  {BOLD}DuckDB total: {duckdb_total_ms:.0f}ms across "
        f"{len(taxon_filters)} quer{'y' if len(taxon_filters) == 1 else 'ies'}{RESET}"
    )

    non_empty_groups = [g for g in groups if g]
    if not non_empty_groups:
        pipeline_elapsed_s = time.perf_counter() - pipeline_start
        print(f"\n{BOLD}Pipeline total: {pipeline_elapsed_s * 1000:.0f}ms (no_results){RESET}")
        return {
            "status": "no_results",
            "taxonFilters": taxon_filters,
            "species": [],
            "message": "We understood your request, but didn't find anything for it here right now.",
            "timings_ms": {
                "llm": llm_elapsed_s * 1000,
                "duckdb_total": duckdb_total_ms,
                "duckdb_per_filter": duckdb_query_times_ms,
                "pipeline_total": pipeline_elapsed_s * 1000,
            },
        }

    print(f"\n{BOLD}--- STEP 4: select across groups + order waypoints ---{RESET}")
    selected = _select_species_across_groups(non_empty_groups, TOP_SPECIES_COUNT)
    center_lat, center_lon = polygon_centroid(RETIRO_POLYGON)
    ordered = order_waypoints(selected, center_lat, center_lon)
    for i, sp in enumerate(ordered, 1):
        print(f"  {i}. {sp['species']:<40} {sp['count']:>6} obs")

    pipeline_elapsed_s = time.perf_counter() - pipeline_start
    print(
        f"\n{BOLD}Pipeline total: {pipeline_elapsed_s * 1000:.0f}ms "
        f"(llm={llm_elapsed_s * 1000:.0f}ms, duckdb={duckdb_total_ms:.0f}ms){RESET}"
    )

    return {
        "status": "resolved",
        "taxonFilters": taxon_filters,
        "species": ordered,
        "message": "This is a DuckDB-backed local-index prototype — no GBIF live calls were made, and no common names yet (batch job not built).",
        "timings_ms": {
            "llm": llm_elapsed_s * 1000,
            "duckdb_total": duckdb_total_ms,
            "duckdb_per_filter": duckdb_query_times_ms,
            "pipeline_total": pipeline_elapsed_s * 1000,
        },
    }


def main():
    if len(sys.argv) < 2:
        print('Usage: python duckdb_e2e_prototype.py "<query>"')
        sys.exit(1)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — export it before running this script.")
        sys.exit(1)
    if not os.path.exists(FACT_PARQUET):
        print(f"{FACT_PARQUET} not found — run duckdb_parquet_prototype.py --export first.")
        sys.exit(1)

    result = run_query(sys.argv[1])
    print(f"\n{GREEN}status={result['status']}{RESET}")


if __name__ == "__main__":
    main()
