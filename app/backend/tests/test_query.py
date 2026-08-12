from fastapi.testclient import TestClient

from main import app
from models.query import QueryRequest

client = TestClient(app)

RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)


def test_consent_defaults_to_false_when_omitted():
    request = QueryRequest(query="birds", distinctId="anon-1", polygon=RETIRO_POLYGON)

    assert request.consent is False


def test_consent_true_is_preserved():
    request = QueryRequest(
        query="birds", distinctId="anon-1", consent=True, polygon=RETIRO_POLYGON
    )

    assert request.consent is True


def test_empty_query_returns_422():
    response = client.post(
        "/api/query",
        json={"query": "", "distinctId": "anon-123"},
    )

    assert response.status_code == 422


def test_whitespace_only_query_returns_422():
    response = client.post(
        "/api/query",
        json={"query": "   ", "distinctId": "anon-123"},
    )

    assert response.status_code == 422


def test_query_over_max_length_returns_422():
    response = client.post(
        "/api/query",
        json={"query": "a" * 301, "distinctId": "anon-123"},
    )

    assert response.status_code == 422
