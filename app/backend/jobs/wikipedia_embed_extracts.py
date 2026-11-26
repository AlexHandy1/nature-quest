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


def _default_embed_fn(texts: list[str]) -> list[list[float]]:
    """Real fastembed/ONNX call - validated in
    prototypes/scripts/semantic_search_index_fastembed.py. Not unit tested
    directly (loads/downloads a real model); run() takes embed_fn so tests
    inject a fake instead."""
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=MODEL_NAME)
    return [vector.tolist() for vector in model.embed(texts)]


def run(
    fetch_output_path: str,
    embeddings_output_path: str,
    embed_fn: Callable[[list[str]], list[list[float]]] = _default_embed_fn,
    dim: int = DIM,
) -> dict:
    fetch_results = read_fetch_results(fetch_output_path)
    embeddable = rows_to_embed(fetch_results)
    logger.info(
        "wikipedia_embed_extracts_starting",
        extra={"total_fetched": len(fetch_results), "embeddable": len(embeddable)},
    )

    embedded_rows = embed_records(embeddable, embed_fn=embed_fn)
    write_embeddings_parquet(embedded_rows, embeddings_output_path, dim=dim)

    summary = {
        "total_fetched": len(fetch_results),
        "embedded": len(embedded_rows),
        "skipped": len(fetch_results) - len(embedded_rows),
    }
    logger.info("wikipedia_embed_extracts_complete", extra=summary)
    return summary


def main() -> None:
    configure_json_logging()
    fetch_output_path = os.environ["WIKIPEDIA_EXTRACTS_OUTPUT_PATH"]
    embeddings_output_path = os.environ["WIKIPEDIA_EMBEDDINGS_OUTPUT_PATH"]
    run(fetch_output_path, embeddings_output_path)


if __name__ == "__main__":
    main()
