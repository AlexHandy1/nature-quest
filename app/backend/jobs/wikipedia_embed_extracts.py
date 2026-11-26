"""Embed step of the Wikipedia-embeddings pipeline (fetch -> embed -> build
table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md). Reads the
fetch step's JSONL output, embeds each species with status="ok" via
fastembed/ONNX MiniLM (sentence-transformers/all-MiniLM-L6-v2, 384-dim,
validated in prototypes/scripts/semantic_search_index_fastembed.py), and
writes specieskey + embedding FLOAT[384] to a parquet file - a standalone
checkpoint of its own, independent of the fetch step's own output file."""

import json
import logging
import os
import sys
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
) -> dict:
    embed_fn = embed_fn or _build_default_embed_fn()
    fetch_results = read_fetch_results(fetch_output_path)
    embeddable = rows_to_embed(fetch_results)
    logger.info(
        "wikipedia_embed_extracts_starting",
        extra={"total_fetched": len(fetch_results), "embeddable": len(embeddable)},
    )
    print(f"Starting embed: {len(embeddable)}/{len(fetch_results)} species have a usable extract", flush=True)

    chunks = chunk(embeddable, chunk_size)
    embedded_rows: list[dict] = []
    for i, batch in enumerate(chunks, start=1):
        embedded_rows.extend(embed_records(batch, embed_fn=embed_fn))
        percent_complete = round(len(embedded_rows) / len(embeddable) * 100, 1) if embeddable else 0.0
        logger.info(
            "wikipedia_embed_extracts_progress",
            extra={
                "chunks_done": i,
                "total_chunks": len(chunks),
                "species_embedded": len(embedded_rows),
                "total_embeddable": len(embeddable),
                "percent_complete": percent_complete,
            },
        )
        print(
            f"[chunk {i}/{len(chunks)}] species embedded: "
            f"{len(embedded_rows)}/{len(embeddable)} ({percent_complete}%)",
            flush=True,
        )

    write_embeddings_parquet(embedded_rows, embeddings_output_path, dim=dim)

    summary = {
        "total_fetched": len(fetch_results),
        "embedded": len(embedded_rows),
        "skipped": len(fetch_results) - len(embedded_rows),
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
