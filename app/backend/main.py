import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv

# Must run before the router imports below - routers.query reads required
# env vars (FACT_PARQUET_PATH etc.) at module import time.
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from routers.health import router as health_router
from routers.narration import router as narration_router
from routers.query import router as query_router
from services import ai_observability, species_index, tts
from services.rate_limiter import handle_rate_limit_exceeded, limiter


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


def warm_caches() -> None:
    """Builds the DuckDB/GCS connection and both OpenRouter clients once,
    during startup, instead of on whichever user's request happens to be
    first - see docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md.
    No-op locally (K_SERVICE unset): local dev never touches GCS/Secret
    Manager, and the lazy caches in species_index/tts/ai_observability
    still build themselves correctly on first real use regardless."""
    if not os.environ.get("K_SERVICE"):
        return
    species_index.get_shared_connection()
    api_key = tts.resolve_api_key()
    for consent in (False, True):
        ai_observability.build_openrouter_client(
            consent=consent, distinct_id="startup-warming", api_key=api_key
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs before uvicorn binds the port, so Cloud Run's startup probe (and
    # therefore any real traffic) waits for this - the one-time warming cost
    # lands in the container's pre-traffic boot window, not a user's request.
    warm_caches()
    yield


def create_app(static_dir: Path = DEFAULT_STATIC_DIR) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter
    # Starlette's add_exception_handler is typed for Callable[[Request, Exception], ...];
    # a handler narrowed to a specific exception subclass (as it must be, to distinguish
    # handlers) doesn't satisfy that contravariantly — a known typing limitation, not a bug.
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)  # type: ignore[arg-type]
    app.include_router(health_router)
    app.include_router(query_router)
    app.include_router(narration_router)
    # Any future router must be included above this line — the static mount
    # matches every remaining path, so routes added after it are unreachable.
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
