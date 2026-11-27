"""Embed step of the Wikipedia-embeddings pipeline (fetch -> embed -> build
table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md). Reads the
fetch step's JSONL output, embeds each species with status="ok" via
fastembed/ONNX MiniLM (sentence-transformers/all-MiniLM-L6-v2, 384-dim,
validated in prototypes/scripts/semantic_search_index_fastembed.py), and
writes specieskey + embedding FLOAT[384] to a parquet file - a standalone
checkpoint of its own, independent of the fetch step's own output file.

Checkpointing: unlike the fetch step's JSONL output, parquet has no native
append, so each chunk is written as its own small file in a chunk_dir
(defaults to f"{embeddings_output_path}.chunks"). A restart reads already-
checkpointed specieskeys from chunk_dir and only re-embeds what's missing.
Once every chunk is embedded, chunk_dir's files are streamed into a single
final parquet via DuckDB (disk-to-disk, not held in Python memory)."""

import json
import logging
import os
import sys
import uuid
from collections.abc import Callable

import duckdb
import pandas as pd

logger = logging.getLogger("wikipedia_embed_extracts")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
EMBED_CHUNK_SIZE = 2000  # progress-visibility granularity, not a model batching limit


def chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class JsonLogFormatter(logging.Formatter):
    """Duplicated from jobs/monthly_gbif_update.py rather than shared - each
    job script is a separate, independently runnable process."""

    def format(self, record: logging.LogRecord) -> str:
        reserved = set(logging.makeLogRecord({}).__dict__.keys())
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in reserved})
        return json.dumps(payload)


def configure_json_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def read_fetch_results(fetch_output_path: str) -> list[dict]:
    with open(fetch_output_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def rows_to_embed(fetch_results: list[dict]) -> list[dict]:
    return [row for row in fetch_results if row["status"] == "ok" and row["extract"]]


def records_to_embed(embeddable: list[dict], already_embedded: set[str]) -> list[dict]:
    return [record for record in embeddable if record["specieskey"] not in already_embedded]


def build_embedding_text(record: dict) -> str:
    """Same "<common name> (<scientific name>): <extract>" recipe the 31 Oct
    precision@5 validation was built on (prototypes/scripts/
    semantic_search_index.py), falling back to the scientific name alone
    when a species has no common name."""
    if record.get("common_name"):
        return f"{record['common_name']} ({record['species']}): {record['extract']}"
    return f"{record['species']}: {record['extract']}"


def embed_records(records: list[dict], embed_fn: Callable[[list[str]], list[list[float]]]) -> list[dict]:
    texts = [build_embedding_text(record) for record in records]
    vectors = embed_fn(texts)
    return [
        {"specieskey": record["specieskey"], "embedding": list(vector)}
        for record, vector in zip(records, vectors, strict=True)
    ]


def write_embeddings_parquet(rows: list[dict], output_path: str, dim: int = DIM) -> None:
    con = duckdb.connect()
    con.register("rows_df", pd.DataFrame(rows))
    con.execute(f"""
        COPY (SELECT specieskey, CAST(embedding AS FLOAT[{dim}]) AS embedding FROM rows_df)
        TO '{output_path}' (FORMAT PARQUET)
    """)


def write_chunk_parquet(rows: list[dict], chunk_dir: str, dim: int = DIM) -> None:
    """Writes one chunk's rows as their own parquet file inside chunk_dir -
    the per-chunk checkpoint a crashed run resumes from via
    read_embedded_specieskeys. A fresh UUID filename each call, so resumed
    runs never collide with chunk files left by an earlier attempt."""
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f"{uuid.uuid4().hex}.parquet")
    write_embeddings_parquet(rows, chunk_path, dim=dim)


def read_embedded_specieskeys(chunk_dir: str) -> set[str]:
    if not os.path.exists(chunk_dir) or not os.listdir(chunk_dir):
        return set()
    con = duckdb.connect()
    rows = con.execute(f"SELECT DISTINCT specieskey FROM read_parquet('{chunk_dir}/*.parquet')").fetchall()
    return {row[0] for row in rows}


def _build_default_embed_fn() -> Callable[[list[str]], list[list[float]]]:
    """Loads the real fastembed/ONNX model once (validated in
    prototypes/scripts/semantic_search_index_fastembed.py) and returns a
    closure reusing it across every chunk - not unit tested directly
    (downloads/loads a real model); run() takes embed_fn so tests inject a
    fake instead."""
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=MODEL_NAME)

    def embed_fn(texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in model.embed(texts)]

    return embed_fn


def merge_chunks_to_final(chunk_dir: str, output_path: str, dim: int = DIM) -> None:
    """Streams every chunk file in chunk_dir into a single final parquet via
    DuckDB (disk-to-disk, no Python-side materialization of all rows) - the
    one-time "combine everything" step, done once at the end rather than
    once per chunk, since parquet has no native append."""
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT specieskey, CAST(embedding AS FLOAT[{dim}]) AS embedding
            FROM read_parquet('{chunk_dir}/*.parquet')
        ) TO '{output_path}' (FORMAT PARQUET)
    """)


def check_output_consistency(embeddable_specieskeys: set[str], output_path: str) -> dict:
    """Reads output_path back from disk (not in-memory run() counters) and
    compares its actual row/distinct-specieskey counts against the species
    that were actually eligible to embed."""
    con = duckdb.connect()
    row_count = con.execute(f"SELECT count(*) FROM read_parquet('{output_path}')").fetchone()[0]  # type: ignore[index]
    distinct_rows = con.execute(
        f"SELECT DISTINCT specieskey FROM read_parquet('{output_path}')"
    ).fetchall()
    output_specieskeys = {row[0] for row in distinct_rows}
    return {
        "embeddable_species": len(embeddable_specieskeys),
        "output_row_count": row_count,
        "output_distinct_specieskeys": len(output_specieskeys),
        "rows_match_distinct_keys": row_count == len(output_specieskeys),
        "output_matches_embeddable": output_specieskeys == embeddable_specieskeys,
    }


def run(
    fetch_output_path: str,
    embeddings_output_path: str,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    dim: int = DIM,
    chunk_size: int = EMBED_CHUNK_SIZE,
    chunk_dir: str | None = None,
) -> dict:
    embed_fn = embed_fn or _build_default_embed_fn()
    chunk_dir = chunk_dir or f"{embeddings_output_path}.chunks"
    fetch_results = read_fetch_results(fetch_output_path)
    embeddable = rows_to_embed(fetch_results)
    already_embedded = read_embedded_specieskeys(chunk_dir)
    remaining = records_to_embed(embeddable, already_embedded)
    logger.info(
        "wikipedia_embed_extracts_starting",
        extra={
            "total_fetched": len(fetch_results),
            "embeddable": len(embeddable),
            "already_embedded": len(already_embedded),
            "remaining": len(remaining),
        },
    )
    print(
        f"Starting embed: {len(embeddable)}/{len(fetch_results)} species have a usable extract, "
        f"{len(already_embedded)} already checkpointed, {len(remaining)} remaining",
        flush=True,
    )

    chunks = chunk(remaining, chunk_size)
    embedded_this_run = 0
    for i, batch in enumerate(chunks, start=1):
        embedded_rows = embed_records(batch, embed_fn=embed_fn)
        write_chunk_parquet(embedded_rows, chunk_dir, dim=dim)
        embedded_this_run += len(embedded_rows)
        percent_complete = round(embedded_this_run / len(remaining) * 100, 1) if remaining else 0.0
        logger.info(
            "wikipedia_embed_extracts_progress",
            extra={
                "chunks_done": i,
                "total_chunks": len(chunks),
                "species_embedded": embedded_this_run,
                "total_embeddable": len(remaining),
                "percent_complete": percent_complete,
            },
        )
        print(
            f"[chunk {i}/{len(chunks)}] species embedded: "
            f"{embedded_this_run}/{len(remaining)} ({percent_complete}%)",
            flush=True,
        )

    merge_chunks_to_final(chunk_dir, embeddings_output_path, dim=dim)

    summary = {
        "total_fetched": len(fetch_results),
        "embedded": len(embeddable),
        "skipped": len(fetch_results) - len(embeddable),
    }
    logger.info("wikipedia_embed_extracts_complete", extra=summary)

    embeddable_specieskeys = {record["specieskey"] for record in embeddable}
    consistency = check_output_consistency(embeddable_specieskeys, embeddings_output_path)
    logger.info("wikipedia_embed_extracts_consistency_check", extra=consistency)
    print(
        f"Consistency check: {consistency['output_row_count']} output rows, "
        f"{consistency['output_distinct_specieskeys']} distinct specieskeys, "
        f"embeddable target {consistency['embeddable_species']} "
        f"(matches embeddable: {consistency['output_matches_embeddable']})",
        flush=True,
    )

    return summary


def main() -> None:
    configure_json_logging()
    fetch_output_path = os.environ["WIKIPEDIA_EXTRACTS_OUTPUT_PATH"]
    embeddings_output_path = os.environ["WIKIPEDIA_EMBEDDINGS_OUTPUT_PATH"]
    run(fetch_output_path, embeddings_output_path)


if __name__ == "__main__":
    main()
