"""
PROTOTYPE — semantic_search_index.py (Track A, step 2 of 3)

Question this answers: can an off-the-shelf sentence encoder
(sentence-transformers/all-MiniLM-L6-v2, 384-dim, inference only) embed the
species corpus quickly on CPU, and can a plain DuckDB FLOAT[384] array column
+ array_cosine_similarity serve as the "simple local vector database" for
top-k lookup — keeping the vector store in the same engine as the production
Parquet index (ADR-017) rather than adding a dedicated vector DB?

Embedded text per species: "<common name> (<scientific name>): <extract>".
The hand-assigned experiment tags are deliberately NOT embedded.

Builds:  prototypes/artifacts/semantic_search.duckdb (table `species`)
Reads:   prototypes/artifacts/semantic_search_corpus.json (from semantic_search_corpus.py)
Run:     venv/bin/python prototypes/scripts/semantic_search_index.py | tee prototypes/logs/semantic_search_index_$(date +%Y%m%d_%H%M%S).log

search_store()/load_model()/embed_query() are imported by
server_semantic_search.py and semantic_search_experiment.py (thin wrappers
measuring this exact code path — same justification as server.py importing
e2e_walk_spike_server.py).
"""

import json
import os
import time
from pathlib import Path

import duckdb

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
CORPUS_PATH = ARTIFACTS / "semantic_search_corpus.json"
DB_PATH = ARTIFACTS / "semantic_search.duckdb"

DISPLAY_COLUMNS = ["specieskey", "scientific_name", "common_name", "group", "tier",
                   "occurrence_count", "extract", "image_url", "wiki_url", "tags"]


def build_embedding_text(record: dict) -> str:
    return f"{record['common_name']} ({record['scientific_name']}): {record['extract']}"


def build_store(db_path: str, records: list[dict], vectors: list[list[float]], dim: int = DIM):
    if os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    con.execute(f"""
        CREATE TABLE species (
            specieskey VARCHAR PRIMARY KEY,
            scientific_name VARCHAR,
            common_name VARCHAR,
            "group" VARCHAR,
            tier VARCHAR,
            occurrence_count BIGINT,
            extract VARCHAR,
            image_url VARCHAR,
            wiki_url VARCHAR,
            tags VARCHAR[],
            embedding FLOAT[{dim}]
        )
    """)
    con.executemany(
        "INSERT INTO species VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [[r.get(c) for c in DISPLAY_COLUMNS] + [list(map(float, v))] for r, v in zip(records, vectors)],
    )
    return con


def search_store(con, query_vector: list[float], k: int = 5, dim: int = DIM) -> list[dict]:
    rows = con.execute(f"""
        SELECT specieskey, scientific_name, common_name, "group", tier, occurrence_count,
               extract, image_url, wiki_url, tags,
               array_cosine_similarity(embedding, ?::FLOAT[{dim}]) AS score
        FROM species
        ORDER BY score DESC
        LIMIT ?
    """, [list(map(float, query_vector)), k]).fetchall()
    return [dict(zip(DISPLAY_COLUMNS + ["score"], row)) for row in rows]


def load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME, device="cpu")


def embed_query(model, text: str) -> list[float]:
    return model.encode(text, normalize_embeddings=True).tolist()


def main() -> None:
    corpus = json.loads(CORPUS_PATH.read_text())
    records = corpus["selected"]
    texts = [build_embedding_text(r) for r in records]
    print(f"Embedding {len(records)} species with {MODEL_NAME}\n")

    t0 = time.perf_counter()
    model = load_model()
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"Model load (cached on disk after first download): {load_ms:.0f}ms")

    tokenizer = model.tokenizer
    max_len = model.max_seq_length
    token_counts = [len(tokenizer(t)["input_ids"]) for t in texts]
    truncated = sum(c > max_len for c in token_counts)
    print(f"Token counts: min={min(token_counts)} mean={sum(token_counts)/len(token_counts):.0f} "
          f"max={max(token_counts)}; {truncated}/{len(texts)} exceed max_seq_length={max_len} (truncated)")

    model.encode(texts[:4])  # warm-up so the timed run measures steady-state throughput
    t0 = time.perf_counter()
    vectors = model.encode(texts, batch_size=32, normalize_embeddings=True)
    batch_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for t in texts[:20]:
        model.encode(t, normalize_embeddings=True)
    single_ms = (time.perf_counter() - t0) * 1000 / 20

    print(f"Batch encode (batch_size=32): {batch_ms:.0f}ms total, {batch_ms/len(texts):.1f}ms/species, "
          f"{len(texts)/(batch_ms/1000):.0f} species/s")
    print(f"One-at-a-time encode: {single_ms:.1f}ms/species")

    t0 = time.perf_counter()
    con = build_store(str(DB_PATH), records, vectors.tolist())
    store_ms = (time.perf_counter() - t0) * 1000
    count = con.execute("SELECT count(*) FROM species").fetchone()[0]
    con.close()
    print(f"DuckDB store write: {store_ms:.0f}ms, {count} rows → {DB_PATH} ({DB_PATH.stat().st_size/1024:.0f} KiB)")

    stats = {
        "model": MODEL_NAME, "species": len(records), "model_load_ms": load_ms,
        "batch_encode_ms": batch_ms, "batch_ms_per_species": batch_ms / len(texts),
        "single_encode_ms_per_species": single_ms, "store_write_ms": store_ms,
        "token_count_mean": sum(token_counts) / len(token_counts), "token_count_max": max(token_counts),
        "truncated_count": truncated, "max_seq_length": max_len, "cpu_threads": os.cpu_count(),
    }
    (ARTIFACTS / "semantic_search_index_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
