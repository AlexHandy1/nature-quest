import logging
import time
from contextlib import contextmanager

query_logger = logging.getLogger("query")


def log_query_outcome(
    query: str | None,
    outcome: str,
    distinct_id: str | None = None,
    guardrail: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    gbif_result_count: int | None = None,
    failed_step: str | None = None,
    elapsed_ms: int | None = None,
) -> None:
    query_logger.info(
        "query_outcome",
        extra={
            "query": query,
            "outcome": outcome,
            "distinct_id": distinct_id,
            "guardrail": guardrail,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "gbif_result_count": gbif_result_count,
            "failed_step": failed_step,
            "elapsed_ms": elapsed_ms,
        },
    )


def log_query_submitted(query: str, distinct_id: str, polygon: str) -> None:
    query_logger.info(
        "query_submitted",
        extra={"query": query, "distinct_id": distinct_id, "polygon": polygon},
    )


def log_llm_taxon_filters_resolved(query: str, distinct_id: str, taxon_filters: list[dict]) -> None:
    query_logger.info(
        "llm_taxon_filters_resolved",
        extra={"query": query, "distinct_id": distinct_id, "taxon_filters": taxon_filters},
    )


def log_species_selected(query: str, distinct_id: str, species: list[dict]) -> None:
    query_logger.info(
        "species_selected",
        extra={"query": query, "distinct_id": distinct_id, "species": species},
    )


def log_waypoints_ordered(query: str, distinct_id: str, waypoints: list[dict]) -> None:
    query_logger.info(
        "waypoints_ordered",
        extra={"query": query, "distinct_id": distinct_id, "waypoints": waypoints},
    )


def log_species_enriched(query: str, distinct_id: str, species: list[dict]) -> None:
    query_logger.info(
        "species_enriched",
        extra={"query": query, "distinct_id": distinct_id, "species": species},
    )


def log_timing_checkpoint(checkpoint: str, elapsed_ms: float, **extra) -> None:
    """Permanent per-request latency instrumentation (not a throwaway
    diagnostic) - see docs/specs/spec-duckdb-caching-latency-experiment-211026.md.
    One line per named checkpoint (t_connect, t_query_execute, ...), tagged
    with whatever context the call site has (e.g. consent, taxon_rank)."""
    query_logger.info(
        "timing_checkpoint",
        extra={"checkpoint": checkpoint, "elapsed_ms": elapsed_ms, **extra},
    )


@contextmanager
def timed_checkpoint(checkpoint: str, **extra):
    """Wraps a block, logging its wall-clock duration via log_timing_checkpoint
    on exit - including when the block raises, so a failing step still shows
    up in the timing breakdown instead of silently vanishing."""
    start = time.perf_counter()
    try:
        yield
    finally:
        log_timing_checkpoint(checkpoint, (time.perf_counter() - start) * 1000, **extra)


def log_narration_outcome(
    outcome: str,
    distinct_id: str | None = None,
    guardrail: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    query_logger.info(
        "narration_outcome",
        extra={
            "outcome": outcome,
            "distinct_id": distinct_id,
            "guardrail": guardrail,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
