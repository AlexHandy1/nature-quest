from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_requests_within_the_per_ip_rate_limit_are_not_blocked():
    with patch("routers.query._resolve_taxon_filter", return_value=None):
        for _ in range(10):
            response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})
            assert response.status_code != 429


def test_exceeding_the_per_ip_rate_limit_returns_429():
    with patch("routers.query._resolve_taxon_filter", return_value=None):
        for _ in range(10):
            client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"
