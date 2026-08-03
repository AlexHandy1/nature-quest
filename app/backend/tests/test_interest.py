from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_valid_submission_returns_201_received():
    response = client.post(
        "/api/interest",
        json={"query": "show me some birds near here"},
    )

    assert response.status_code == 201
    assert response.json() == {"status": "received"}


def test_empty_query_returns_422():
    response = client.post(
        "/api/interest",
        json={"query": ""},
    )

    assert response.status_code == 422


def test_oversized_query_returns_422():
    response = client.post(
        "/api/interest",
        json={"query": "a" * 2001},
    )

    assert response.status_code == 422


def test_valid_submission_logs_the_query():
    with patch("routers.interest.log_interest_submission") as mock_log:
        client.post(
            "/api/interest",
            json={"query": "something rare"},
        )

    mock_log.assert_called_once_with(query="something rare")
