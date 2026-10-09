"""
Throwaway prototype — NOT production code.

Follow-up to duckdb_parquet_prototype.py: that test's 8 Retiro candidate
cells all happened to land in a single Parquet row group, so it never
proved pruning across row-group boundaries. This script reuses the same
cached Parquet exports but queries a much larger area (Greater Madrid,
>>25km^2) to force candidate cells to span multiple row groups, and checks
whether DuckDB still prunes the ones in between rather than reading
everything from the first matching group to the last.

Does not re-touch BigQuery — loads the Parquet files already exported by
duckdb_parquet_prototype.py.

Standalone — does not import from other prototype scripts (established
pattern in this codebase).
"""

import time
import tracemalloc

import duckdb
import pygeohash as pgh

FACT_PARQUET = "prototypes/scratch/fact_sorted_by_cell.parquet"
DIM_PARQUET = "prototypes/scratch/species_dimension.parquet"

# Greater Madrid bounding box — roughly the city + surrounding municipalities,
# well over 25km^2 (approx 30km x 25km here).
MADRID_MIN_LAT, MADRID_MAX_LAT = 40.31, 40.57
MADRID_MIN_LON, MADRID_MAX_LON = -3.83, -3.52
GEOHASH_PRECISION = 6


def candidate_cells(min_lat, max_lat, min_lon, max_lon, precision=GEOHASH_PRECISION, step_deg=0.01):
    cells = set()
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            cells.add(pgh.encode(lat, lon, precision=precision))
            lon += step_deg
        lat += step_deg
    return sorted(cells)


def row_groups_touched(con, parquet_path, cells):
    cell_list = ", ".join(f"'{c}'" for c in cells)
    meta = con.execute(f"""
        SELECT row_group_id, MIN(stats_min_value) AS cell_min, MAX(stats_max_value) AS cell_max
        FROM parquet_metadata('{parquet_path}')
        WHERE path_in_schema = 'cell'
        GROUP BY row_group_id
        ORDER BY row_group_id
    """).fetchdf()
    touched = []
    for _, row in meta.iterrows():
        if any(row["cell_min"] <= c <= row["cell_max"] for c in cells):
            touched.append(row["row_group_id"])
    return touched, len(meta)


def run_prototype():
    cells = candidate_cells(MADRID_MIN_LAT, MADRID_MAX_LAT, MADRID_MIN_LON, MADRID_MAX_LON)
    print(f"Candidate cells for Greater Madrid bbox (precision {GEOHASH_PRECISION}): {len(cells)}", flush=True)
    print(f"  {cells}", flush=True)

    con = duckdb.connect()

    touched, total_groups = row_groups_touched(con, FACT_PARQUET, cells)
    print(f"\nRow groups whose [min,max] cell range could contain a candidate cell: "
          f"{len(touched)} of {total_groups}", flush=True)
    print(f"  touched row group ids: {touched}", flush=True)

    cell_list = ", ".join(f"'{c}'" for c in cells)
    query = f"""
        SELECT s.species, s.kingdom, s.class, SUM(f.occurrence_count) AS total
        FROM read_parquet('{FACT_PARQUET}') f
        JOIN read_parquet('{DIM_PARQUET}') s ON f.specieskey = s.specieskey
        WHERE f.cell IN ({cell_list})
        GROUP BY s.species, s.kingdom, s.class
        ORDER BY total DESC
        LIMIT 5
    """

    print("\n--- EXPLAIN ANALYZE (row group pruning check) ---", flush=True)
    plan = con.execute(f"EXPLAIN ANALYZE {query}").fetchall()
    for row in plan:
        print(row[-1], flush=True)

    tracemalloc.start()
    start = time.perf_counter()
    result = con.execute(query).fetchall()
    elapsed = time.perf_counter() - start
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("\n--- Result ---", flush=True)
    for row in result:
        print(row, flush=True)

    print(f"\nQuery latency: {elapsed:.4f}s", flush=True)
    print(f"Peak Python-side memory during query: {peak_mem / 1024 / 1024:.2f} MB", flush=True)


if __name__ == "__main__":
    run_prototype()
