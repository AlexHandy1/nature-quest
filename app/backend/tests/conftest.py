import pytest

from main import app
from services import query_budget


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    app.state.limiter.reset()
    yield


@pytest.fixture(autouse=True)
def reset_query_budget():
    query_budget.reset()
    yield
