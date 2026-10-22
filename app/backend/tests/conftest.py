import pytest

from main import app
from services import (
    ai_observability,
    narration_budget,
    query_budget,
    species_index,
    tts,
)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    app.state.limiter.reset()
    yield


@pytest.fixture(autouse=True)
def reset_query_budget():
    query_budget.reset()
    yield


@pytest.fixture(autouse=True)
def reset_narration_budget():
    narration_budget.reset()
    yield


@pytest.fixture(autouse=True)
def reset_ai_observability_client():
    ai_observability.reset()
    yield


@pytest.fixture(autouse=True)
def reset_shared_index_connection():
    species_index.reset_shared_connection()
    yield
    species_index.reset_shared_connection()


@pytest.fixture(autouse=True)
def reset_tts_api_key_cache():
    tts.reset()
    yield
    tts.reset()
