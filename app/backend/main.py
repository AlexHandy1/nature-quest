import json
import logging
import sys
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from routers.health import router as health_router
from routers.query import router as query_router
from services.rate_limiter import handle_rate_limit_exceeded, limiter

load_dotenv()


class JsonLogFormatter(logging.Formatter):
    """Renders log records as one JSON line per entry, so Cloud Run's stdout
    capture parses them into structured (queryable) Cloud Logging fields
    instead of one opaque text blob."""

    _RESERVED_KEYS: ClassVar[set[str]] = set(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in self._RESERVED_KEYS}
        )
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])

DEFAULT_STATIC_DIR = Path(__file__).parent / "static"


def create_app(static_dir: Path = DEFAULT_STATIC_DIR) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    # Starlette's add_exception_handler is typed for Callable[[Request, Exception], ...];
    # a handler narrowed to a specific exception subclass (as it must be, to distinguish
    # handlers) doesn't satisfy that contravariantly — a known typing limitation, not a bug.
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)  # type: ignore[arg-type]
    app.include_router(health_router)
    app.include_router(query_router)
    # Any future router must be included above this line — the static mount
    # matches every remaining path, so routes added after it are unreachable.
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
