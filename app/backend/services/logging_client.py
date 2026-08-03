import logging

logger = logging.getLogger("interest_capture")


def log_interest_submission(query: str) -> None:
    logger.info("interest_submission_received", extra={"query": query})
