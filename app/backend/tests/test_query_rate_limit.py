from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_requests_within_the_per_ip_rate_limit_are_not_blocked():
    with patch("routers.query._resolve_taxon_filters", return_value=([], {})):
        for _ in range(10):
            response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})
            assert response.status_code != 429


def test_exceeding_the_per_ip_rate_limit_returns_429():
    with patch("routers.query._resolve_taxon_filters", return_value=([], {})):
        for _ in range(10):
            client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"


def test_exceeding_the_per_ip_rate_limit_logs_the_guardrail():
    with (
        patch("routers.query._resolve_taxon_filters", return_value=([], {})),
        patch("services.rate_limiter.log_query_outcome") as mock_log,
    ):
        for _ in range(10):
            client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

        client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    mock_log.assert_called_once_with(
        "birds", "rate_limited", distinct_id="anon-1", guardrail="rate_limit"
    )
