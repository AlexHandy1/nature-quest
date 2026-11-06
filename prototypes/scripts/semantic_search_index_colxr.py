"""
PROTOTYPE — semantic_search_index_colxr.py

Question this answers: the 100-species corpus (semantic_search_corpus.json)
was built with GBIF's numeric backbone usageKey as `specieskey` (via the live
species/match API — see semantic_search_corpus.py's own docstring). The
production fact/dimension parquet tables key species by Catalogue of Life
(COL XR) ids instead (e.g. Pica pica -> '4HPXM', not a numeric key — see
prototypes/README.md Section 12). Those are different ID spaces with no
direct mapping, which blocks the one thing routing_cascade.py needs to do:
join "species present in these candidate cells" (COL XR-keyed, from the fact
table) against "species with a precomputed embedding" (whatever key the
embedding store uses).

Fix validated here: re-key the embedding corpus by COL XR id, resolved via a
one-time join on scientific name against the dimension table — exactly the
approach the real offline Wikipedia-embedding batch job should use (start
from the dimension table's own species list + COL XR keys, not from a fresh
GBIF species/match call per species, which is what produced the mismatch in
the first place).

Species whose scientific name doesn't resolve to a dimension-table row are
dropped (logged) — they could never surface via the cell-restricted join
anyway, so excluding them from the embedding store is correct, not a loss.

Builds:  prototypes/artifacts/semantic_search_colxr.duckdb (table `species`,
         specieskey = COL XR id)
Reads:   prototypes/artifacts/semantic_search_corpus.json,
         DIMENSION_PARQUET_PATH (.env)
Run:     venv/bin/python prototypes/scripts/semantic_search_index_colxr.py \
           | tee prototypes/logs/semantic_search_index_colxr_$(date +%Y%m%d_%H%M%S).log
"""

import json
import os
import time
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from semantic_search_index import DISPLAY_COLUMNS, build_embedding_text, build_store
from semantic_search_index_fastembed import embed_query, load_model

load_dotenv()

DIM = 384
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
CORPUS_PATH = ARTIFACTS / "semantic_search_corpus.json"
DB_PATH = ARTIFACTS / "semantic_search_colxr.duckdb"
DIMENSION_PARQUET_PATH = os.environ["DIMENSION_PARQUET_PATH"]


def resolve_colxr_keys(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Joins each record's scientific_name against the dimension table on
    LOWER(species) to find its COL XR specieskey. DISTINCT ON picks one row
    per name since a handful of scientific names map to >1 dimension-table
    row (synonyms/subspecies rolled into the same name)."""
    con = duckdb.connect()
    names = [r["scientific_name"] for r in records]
    rows = con.execute(
        """
        SELECT DISTINCT ON (LOWER(c.name)) c.name, d.specieskey
        FROM (SELECT UNNEST(?) AS name) c
        LEFT JOIN read_parquet(?) d ON LOWER(d.species) = LOWER(c.name)
        """,
        [names, DIMENSION_PARQUET_PATH],
    ).fetchall()
    colxr_by_name = {name: key for name, key in rows}

    resolved, dropped = [], []
    for r in records:
        colxr_key = colxr_by_name.get(r["scientific_name"])
        if colxr_key is None:
            dropped.append(r["scientific_name"])
            continue
        resolved.append({**r, "specieskey": colxr_key, "gbif_usage_key": r["specieskey"]})
    return resolved, dropped


def main() -> None:
    corpus = json.loads(CORPUS_PATH.read_text())
    records = corpus["selected"]
    print(f"Resolving COL XR keys for {len(records)} species against {DIMENSION_PARQUET_PATH}\n")

    resolved, dropped = resolve_colxr_keys(records)
    print(f"Resolved {len(resolved)}/{len(records)}; dropped (no dimension-table match): {dropped}\n")

    texts = [build_embedding_text(r) for r in resolved]
    model = load_model()
    vectors = [embed_query(model, t) for t in texts]

    t0 = time.perf_counter()
    con = build_store(str(DB_PATH), resolved, vectors, dim=DIM)
    store_ms = (time.perf_counter() - t0) * 1000
    count = con.execute("SELECT count(*) FROM species").fetchone()[0]
    con.close()
    print(f"DuckDB store write: {store_ms:.0f}ms, {count} rows → {DB_PATH} "
          f"({DB_PATH.stat().st_size / 1024:.0f} KiB)")
    print(f"\nColumns: {DISPLAY_COLUMNS} (specieskey is now the COL XR id)")


if __name__ == "__main__":
    main()
