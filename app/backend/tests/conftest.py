import pytest

from main import app


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    app.state.limiter.reset()
    yield
