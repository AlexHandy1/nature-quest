from fastapi.testclient import TestClient

from main import app
from models.query import QueryRequest

client = TestClient(app)


def test_consent_defaults_to_false_when_omitted():
    request = QueryRequest(query="birds", distinctId="anon-1")

    assert request.consent is False


def test_consent_true_is_preserved():
    request = QueryRequest(query="birds", distinctId="anon-1", consent=True)

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
