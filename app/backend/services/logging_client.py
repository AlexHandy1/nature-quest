import logging

logger = logging.getLogger("interest_capture")
query_logger = logging.getLogger("query")


def log_interest_submission(query: str) -> None:
    logger.info("interest_submission_received", extra={"query": query})


def log_query_outcome(
    query: str | None,
    outcome: str,
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
            "guardrail": guardrail,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "gbif_result_count": gbif_result_count,
        },
    )
