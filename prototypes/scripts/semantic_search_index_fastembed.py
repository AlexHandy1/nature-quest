"""
PROTOTYPE — semantic_search_index_fastembed.py

Same corpus, same embedding text recipe, same DuckDB store/search shape as
semantic_search_index.py, but generating and querying embeddings with
fastembed/ONNX (sentence-transformers/all-MiniLM-L6-v2) instead of
sentence-transformers/torch — see
prototypes/artifacts/semantic_search_fastembed_cold_start_results.json for
the isolated cold-start comparison this builds on.

Builds:  prototypes/artifacts/semantic_search_fastembed.duckdb (table `species`)
Reads:   prototypes/artifacts/semantic_search_corpus.json (from semantic_search_corpus.py)
Run:     venv/bin/python prototypes/scripts/semantic_search_index_fastembed.py
"""

import json
import os
import time
from pathlib import Path

from semantic_search_index import DISPLAY_COLUMNS, build_embedding_text, build_store

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
CORPUS_PATH = ARTIFACTS / "semantic_search_corpus.json"
DB_PATH = ARTIFACTS / "semantic_search_fastembed.duckdb"


def load_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL_NAME)


def embed_query(model, text: str) -> list[float]:
    return next(model.embed([text])).tolist()


def main() -> None:
    corpus = json.loads(CORPUS_PATH.read_text())
    records = corpus["selected"]
    texts = [build_embedding_text(r) for r in records]
    print(f"Embedding {len(records)} species with {MODEL_NAME} (fastembed/ONNX)\n")

    t0 = time.perf_counter()
    model = load_model()
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"Model load (cached on disk after first download): {load_ms:.0f}ms")

    list(model.embed(texts[:4]))  # warm-up so the timed run measures steady-state throughput
    t0 = time.perf_counter()
    vectors = list(model.embed(texts))
    batch_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for t in texts[:20]:
        next(model.embed([t]))
    single_ms = (time.perf_counter() - t0) * 1000 / 20

    print(f"Batch embed: {batch_ms:.0f}ms total, {batch_ms/len(texts):.1f}ms/species, "
          f"{len(texts)/(batch_ms/1000):.0f} species/s")
    print(f"One-at-a-time embed: {single_ms:.1f}ms/species")

    t0 = time.perf_counter()
    con = build_store(str(DB_PATH), records, [v.tolist() for v in vectors], dim=DIM)
    store_ms = (time.perf_counter() - t0) * 1000
    count = con.execute("SELECT count(*) FROM species").fetchone()[0]
    con.close()
    print(f"DuckDB store write: {store_ms:.0f}ms, {count} rows → {DB_PATH} ({DB_PATH.stat().st_size/1024:.0f} KiB)")

    stats = {
        "model": MODEL_NAME, "backend": "fastembed/onnxruntime", "species": len(records),
        "model_load_ms": load_ms, "batch_embed_ms": batch_ms,
        "batch_ms_per_species": batch_ms / len(texts), "single_embed_ms_per_species": single_ms,
        "store_write_ms": store_ms, "cpu_threads": os.cpu_count(),
    }
    (ARTIFACTS / "semantic_search_index_fastembed_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
