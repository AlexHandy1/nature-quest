import json
import logging
import sys
from typing import ClassVar

logger = logging.getLogger("monthly_gbif_update")


class JsonLogFormatter(logging.Formatter):
    """Renders log records as one JSON line per entry, so Cloud Run's stdout
    capture parses them into structured (queryable) Cloud Logging fields
    instead of one opaque text blob. Duplicated from main.py rather than
    shared - the Cloud Run Job entrypoint using this module is a separate
    process that never imports main.py."""

    _RESERVED_KEYS: ClassVar[set[str]] = set(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in self._RESERVED_KEYS}
        )
        return json.dumps(payload)


def configure_json_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def log_monthly_gbif_update_outcome(
    verdict: str,
    block_reasons: list[str] | None = None,
    warn_reasons: list[str] | None = None,
) -> None:
    logger.info(
        "monthly_gbif_update_outcome",
        extra={
            "verdict": verdict,
            "block_reasons": block_reasons or [],
            "warn_reasons": warn_reasons or [],
        },
    )
