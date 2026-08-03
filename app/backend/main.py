import json
import logging
import sys

from fastapi import FastAPI

from routers.interest import router as interest_router


class JsonLogFormatter(logging.Formatter):
    """Renders log records as one JSON line per entry, so Cloud Run's stdout
    capture parses them into structured (queryable) Cloud Logging fields
    instead of one opaque text blob."""

    _RESERVED_KEYS = set(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in self._RESERVED_KEYS}
        )
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])

app = FastAPI()
app.include_router(interest_router)
