"""
PROTOTYPE — semantic_search_scale_probe.py

Question this answers: does brute-force array_cosine_similarity in DuckDB stay
fast enough if the vector table grows from the 100-species prototype to the
full ~106k-species dimension table — both unrestricted, and restricted to a
candidate-cell-sized subset (the planned production shape: search only species
present in the drawn polygon)? Uses SYNTHETIC random unit vectors, so it
measures search cost only — no Wikipedia fetch, no embedding, no quality claim.

Run: venv/bin/python prototypes/scripts/semantic_search_scale_probe.py | tee prototypes/logs/semantic_search_scale_probe_$(date +%Y%m%d_%H%M%S).log
"""

import statistics
import time

import duckdb
import numpy as np

DIM = 384
SIZES = [100, 1_000, 10_000, 106_000]
SUBSET = 2_000  # rough upper bound on distinct species in a large drawn polygon
REPEATS = 20


def unit_rows(n, rng):
    v = rng.standard_normal((n, DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def time_query(con, sql, params):
    times = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        con.execute(sql, params).fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), sorted(times)[int(0.95 * (REPEATS - 1))]


def main():
    rng = np.random.default_rng(0)
    con = duckdb.connect()
    query = unit_rows(1, rng)[0].tolist()
    print(f"{'rows':>8} {'full p50':>10} {'full p95':>10} {'subset p50':>11} {'subset p95':>11}  (ms, k=5)")
    for n in SIZES:
        vecs = unit_rows(n, rng)
        con.execute("DROP TABLE IF EXISTS v")
        con.execute(f"CREATE TABLE v (id INTEGER, embedding FLOAT[{DIM}])")
        con.execute(f"INSERT INTO v SELECT * FROM (SELECT unnest(range(?)) AS id, unnest(?::FLOAT[{DIM}][]) AS embedding)",
                    [n, vecs.tolist()])
        full = time_query(con, f"SELECT id FROM v ORDER BY array_cosine_similarity(embedding, ?::FLOAT[{DIM}]) DESC LIMIT 5", [query])
        ids = rng.choice(n, size=min(SUBSET, n), replace=False).tolist()
        subset = time_query(con, f"SELECT id FROM v WHERE id IN (SELECT unnest(?::INTEGER[])) "
                                 f"ORDER BY array_cosine_similarity(embedding, ?::FLOAT[{DIM}]) DESC LIMIT 5", [ids, query])
        print(f"{n:>8} {full[0]:>10.1f} {full[1]:>10.1f} {subset[0]:>11.1f} {subset[1]:>11.1f}")
    print(f"\nRaw float32 vector storage at 106k: {106_000 * DIM * 4 / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
