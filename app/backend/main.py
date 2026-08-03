import json
import logging
import sys

from fastapi import FastAPI, status
from pydantic import BaseModel, Field

from services.logging_client import log_interest_submission


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


class InterestSubmission(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    analytics_consent: bool


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/api/interest", status_code=status.HTTP_201_CREATED)
def submit_interest(submission: InterestSubmission):
    log_interest_submission(query=submission.query)
    return {"status": "received"}
