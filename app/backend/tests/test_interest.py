from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_valid_submission_returns_201_received():
    response = client.post(
        "/api/interest",
        json={"query": "show me some birds near here", "analytics_consent": False},
    )

    assert response.status_code == 201
    assert response.json() == {"status": "received"}


def test_empty_query_returns_422():
    response = client.post(
        "/api/interest",
        json={"query": "", "analytics_consent": False},
    )

    assert response.status_code == 422


def test_oversized_query_returns_422():
    response = client.post(
        "/api/interest",
        json={"query": "a" * 2001, "analytics_consent": False},
    )

    assert response.status_code == 422


def test_valid_submission_logs_the_query_regardless_of_consent():
    with patch("main.log_interest_submission") as mock_log:
        client.post(
            "/api/interest",
            json={"query": "something rare", "analytics_consent": False},
        )

    mock_log.assert_called_once_with(query="something rare")
