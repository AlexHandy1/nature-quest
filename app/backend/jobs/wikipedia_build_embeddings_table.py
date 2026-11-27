"""Table-assembly step of the Wikipedia-embeddings pipeline (fetch -> embed
-> build table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md).
Reads the embed step's finished parquet and the production manifest, checks
coverage/duplicate guardrails, and writes the final upload-ready table:
specieskey + embedding FLOAT[384] only - deliberately minimal, matching
PLANNING_EMBEDDINGS_FEATURES_291026.md's own description ("a vector column
keyed by specieskey, joinable to species_dimension_with_common_names.parquet").
Species name/common name/extract are already available via that join, so
they aren't duplicated into this table."""

import json
import logging
import os
import sys

import duckdb

logger = logging.getLogger("wikipedia_build_embeddings_table")


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


def read_specieskeys(con: duckdb.DuckDBPyConnection, parquet_path: str) -> set[str]:
    rows = con.execute(f"SELECT DISTINCT specieskey FROM read_parquet('{parquet_path}')").fetchall()
    return {row[0] for row in rows}


def verify_no_duplicate_specieskeys(con: duckdb.DuckDBPyConnection, parquet_path: str) -> None:
    """Raises if the embed step's output has the same specieskey more than
    once - a mechanism failure (the embed step should produce at most one
    row per species), not a data-quality signal, so this is a hard failure
    rather than a soft guardrail outcome."""
    rows = con.execute(f"""
        SELECT specieskey, COUNT(*) AS n FROM read_parquet('{parquet_path}')
        GROUP BY specieskey HAVING COUNT(*) > 1
    """).fetchall()
    if rows:
        duplicate_keys = sorted(row[0] for row in rows)
        raise ValueError(f"Embeddings file has duplicate specieskeys: {duplicate_keys}")


def write_table(con: duckdb.DuckDBPyConnection, embeddings_path: str, output_path: str) -> None:
    con.execute(f"""
        COPY (SELECT specieskey, embedding FROM read_parquet('{embeddings_path}') ORDER BY specieskey)
        TO '{output_path}' (FORMAT PARQUET)
    """)


def compute_coverage_stats(manifest_specieskeys: set[str], embedded_specieskeys: set[str]) -> dict:
    manifest_count = len(manifest_specieskeys)
    embedded_count = len(embedded_specieskeys & manifest_specieskeys)
    coverage_pct = (embedded_count / manifest_count * 100) if manifest_count else 0.0
    return {
        "manifest_species": manifest_count,
        "embedded_species": embedded_count,
        "coverage_pct": coverage_pct,
    }


def run(manifest_path: str, embeddings_path: str, output_path: str) -> dict:
    con = duckdb.connect()
    verify_no_duplicate_specieskeys(con, embeddings_path)

    manifest_specieskeys = read_specieskeys(con, manifest_path)
    embedded_specieskeys = read_specieskeys(con, embeddings_path)
    stats = compute_coverage_stats(manifest_specieskeys, embedded_specieskeys)
    logger.info("wikipedia_build_embeddings_table_coverage", extra=stats)

    write_table(con, embeddings_path, output_path)
    logger.info("wikipedia_build_embeddings_table_complete", extra={"output_path": output_path})
    return stats


def main() -> None:
    configure_json_logging()
    manifest_path = os.environ["WIKIPEDIA_EXTRACTS_MANIFEST_PATH"]
    embeddings_path = os.environ["WIKIPEDIA_EMBEDDINGS_OUTPUT_PATH"]
    output_path = os.environ["WIKIPEDIA_EMBEDDINGS_TABLE_OUTPUT_PATH"]
    run(manifest_path, embeddings_path, output_path)


if __name__ == "__main__":
    main()
