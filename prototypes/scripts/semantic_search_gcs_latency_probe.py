"""
PROTOTYPE — semantic_search_gcs_latency_probe.py

Question this answers: does array_cosine_similarity ranking stay fast when
the embedding table is read over GCS/httpfs the way production reads the
fact/dimension tables, at the real ~240k-species scale confirmed by
wikipedia_coverage_check.py? semantic_search_scale_probe.py already showed
this is fast in-memory/local; what's untested is the network-backed read
itself — unprunable (array_cosine_similarity must touch every row before
sorting, unlike a WHERE-filtered fact-table lookup) and read from the real
GCS bucket, through the real shared-connection/httpfs setup
(services.species_index.open_index_connection), not a local DuckDB table.

Reads the fixture already uploaded by semantic_search_gcs_upload_fixture.py
(run that first) — this script is read-only against the bucket: no upload,
no delete. Synthetic random unit vectors keyed by the real COL XR
specieskeys from wikipedia_coverage_match_manifest.parquet (239,880 rows) —
measures read/scan cost only, no embedding quality claim.

Reads it back via the existing read-only HMAC secret, exactly like
open_index_connection() does in production (forced here regardless of
K_SERVICE, since the point is to exercise the real network path).

Run: venv/bin/python prototypes/scripts/semantic_search_gcs_latency_probe.py
"""

import statistics
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app" / "backend"))

from services.secret_manager_client import fetch_secret  # noqa: E402
from services.species_index import GCS_HMAC_KEY_ID_SECRET, GCS_HMAC_SECRET_SECRET  # noqa: E402

DIM = 384
REPEATS = 20
SUBSET = 2_000  # rough upper bound on distinct species in a large drawn polygon

BUCKET = "nature-quest-504414-index-data"
GCS_URI = f"gs://{BUCKET}/test/synthetic_embeddings_239880.parquet"


def open_gcs_connection() -> duckdb.DuckDBPyConnection:
    """Same shape as services.species_index.open_index_connection's
    K_SERVICE branch, forced here regardless of environment — the point of
    this script is to exercise the real network path from wherever it runs."""
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    key_id = fetch_secret(GCS_HMAC_KEY_ID_SECRET)
    secret = fetch_secret(GCS_HMAC_SECRET_SECRET)
    con.execute(f"CREATE SECRET (TYPE GCS, KEY_ID '{key_id}', SECRET '{secret}')")
    return con


def time_query(con, sql, params, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        con.execute(sql, params).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    return times


def main():
    print("Opening GCS/httpfs connection (shared, reused across all queries below)...")
    con = open_gcs_connection()

    print(f"Row count check against {GCS_URI}...")
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{GCS_URI}')").fetchone()[0]
    print(f"  {n} rows")

    rng = np.random.default_rng(1)
    query_vec = rng.standard_normal(DIM).astype(np.float32)
    query_vec /= np.linalg.norm(query_vec)
    query_vec = query_vec.tolist()

    subset_keys = con.execute(
        f"SELECT specieskey FROM read_parquet('{GCS_URI}') USING SAMPLE {SUBSET} ROWS"
    ).fetchall()
    subset_keys = [k[0] for k in subset_keys]

    full_sql = (
        f"SELECT specieskey FROM read_parquet('{GCS_URI}') "
        f"ORDER BY array_cosine_similarity(embedding::FLOAT[{DIM}], ?::FLOAT[{DIM}]) DESC LIMIT 5"
    )
    subset_sql = (
        f"SELECT specieskey FROM read_parquet('{GCS_URI}') "
        f"WHERE specieskey IN (SELECT UNNEST(?::VARCHAR[])) "
        f"ORDER BY array_cosine_similarity(embedding::FLOAT[{DIM}], ?::FLOAT[{DIM}]) DESC LIMIT 5"
    )

    print("\nCold (first query on this connection):")
    cold_full = time_query(con, full_sql, [query_vec], repeats=1)[0]
    print(f"  full-table first query:        {cold_full:,.1f} ms")

    con2 = con.cursor()
    cold_subset = time_query(con2, subset_sql, [subset_keys, query_vec], repeats=1)[0]
    print(f"  subset-restricted first query: {cold_subset:,.1f} ms")

    print(f"\nWarm (median / p95 of {REPEATS} reps, same shared connection):")
    full_times = time_query(con, full_sql, [query_vec])
    full_p50, full_p95 = statistics.median(full_times), sorted(full_times)[int(0.95 * (REPEATS - 1))]
    print(f"  full-table ({n} rows):          p50={full_p50:,.1f} ms  p95={full_p95:,.1f} ms")

    subset_times = time_query(con2, subset_sql, [subset_keys, query_vec])
    subset_p50, subset_p95 = statistics.median(subset_times), sorted(subset_times)[int(0.95 * (REPEATS - 1))]
    print(f"  subset-restricted ({len(subset_keys)} rows): p50={subset_p50:,.1f} ms  p95={subset_p95:,.1f} ms")

    print(f"\nRaw float32 vector storage at {n}: {n * DIM * 4 / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
