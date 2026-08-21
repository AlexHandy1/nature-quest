import logging

query_logger = logging.getLogger("query")


def log_query_outcome(
    query: str | None,
    outcome: str,
    distinct_id: str | None = None,
    guardrail: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    gbif_result_count: int | None = None,
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


def log_narration_outcome(
    outcome: str,
    distinct_id: str | None = None,
    guardrail: str | None = None,
) -> None:
    query_logger.info(
        "narration_outcome",
        extra={"outcome": outcome, "distinct_id": distinct_id, "guardrail": guardrail},
    )
