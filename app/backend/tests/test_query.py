from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


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
