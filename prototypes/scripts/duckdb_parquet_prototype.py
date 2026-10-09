"""
Throwaway prototype — NOT production code.

Question being tested: can a single Cloud Run request prune straight to the
few cells covering a <=25km^2 polygon from the 221M-row/4.71GB BigQuery fact
table, by querying a local sorted Parquet export with DuckDB, without
loading the whole table into memory or re-querying BigQuery live?

Standalone — does not import from other prototype scripts (established
pattern in this codebase).
"""

import time
import tracemalloc

import duckdb
import pygeohash as pgh
from google.cloud import bigquery

PROJECT = "nature-quest-504414"
DATASET = "gbif_exploration"
FACT_TABLE = f"{PROJECT}.{DATASET}.species_cell_fact_g6_min2"

FACT_PARQUET = "prototypes/scratch/fact_sorted_by_cell.parquet"
DIM_PARQUET = "prototypes/scratch/species_dimension.parquet"

# Same fixed Retiro Park polygon used by waypoint_spike.py
GBIF_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
GEOHASH_PRECISION = 6


def polygon_bbox(wkt_polygon):
    coords_str = wkt_polygon.split("((")[1].split("))")[0]
    points = [tuple(map(float, pair.split())) for pair in coords_str.split(",")]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), max(lats), min(lons), max(lons)


def candidate_cells(wkt_polygon, precision=GEOHASH_PRECISION, step_deg=0.001):
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


def export_from_bigquery():
    print("Connecting to BigQuery client...", flush=True)
    client = bigquery.Client(project=PROJECT)

    print("Submitting fact table export query (sorted by cell)...", flush=True)
    fact_query = f"SELECT cell, specieskey, occurrence_count FROM `{FACT_TABLE}` ORDER BY cell"
    job = client.query(fact_query)
    print(f"  job id: {job.job_id} — waiting for BigQuery to finish executing...", flush=True)
    job.result()
    print("  BigQuery job done — downloading rows into a local dataframe (this is the slow part)...", flush=True)
    fact_df = job.to_dataframe(progress_bar_type="tqdm")
    print(f"  downloaded {len(fact_df):,} rows — writing Parquet...", flush=True)
    fact_df.to_parquet(FACT_PARQUET, index=False)
    print(f"  wrote {FACT_PARQUET}: {len(fact_df):,} rows", flush=True)

    print("Submitting species dimension table export query...", flush=True)
    dim_query = f"""
        SELECT DISTINCT specieskey, species, kingdom, phylum, class, `order`, family, genus
        FROM `{PROJECT}.{DATASET}.species_cell_index_global_g6`
        WHERE specieskey IS NOT NULL
    """
    dim_job = client.query(dim_query)
    print(f"  job id: {dim_job.job_id} — waiting for BigQuery to finish executing...", flush=True)
    dim_job.result()
    print("  BigQuery job done — downloading rows into a local dataframe...", flush=True)
    dim_df = dim_job.to_dataframe(progress_bar_type="tqdm")
    print(f"  downloaded {len(dim_df):,} rows — writing Parquet...", flush=True)
    dim_df.to_parquet(DIM_PARQUET, index=False)
    print(f"  wrote {DIM_PARQUET}: {len(dim_df):,} rows", flush=True)


def run_prototype():
    cells = candidate_cells(GBIF_POLYGON)
    print(f"\nCandidate cells for Retiro polygon (precision {GEOHASH_PRECISION}): {len(cells)}")
    print(f"  {cells}")

    con = duckdb.connect()

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

    print("\n--- EXPLAIN ANALYZE (row group pruning check) ---")
    plan = con.execute(f"EXPLAIN ANALYZE {query}").fetchall()
    for row in plan:
        print(row[-1])

    tracemalloc.start()
    start = time.perf_counter()
    result = con.execute(query).fetchall()
    elapsed = time.perf_counter() - start
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("\n--- Result ---")
    for row in result:
        print(row)

    print(f"\nQuery latency: {elapsed:.4f}s")
    print(f"Peak Python-side memory during query: {peak_mem / 1024 / 1024:.2f} MB")

    import os
    fact_size_mb = os.path.getsize(FACT_PARQUET) / 1024 / 1024
    dim_size_mb = os.path.getsize(DIM_PARQUET) / 1024 / 1024
    print(f"Fact Parquet file size: {fact_size_mb:.2f} MB")
    print(f"Dimension Parquet file size: {dim_size_mb:.2f} MB")


if __name__ == "__main__":
    import os
    import sys

    if "--export" in sys.argv or not os.path.exists(FACT_PARQUET):
        os.makedirs("prototypes/scratch", exist_ok=True)
        export_from_bigquery()

    run_prototype()
