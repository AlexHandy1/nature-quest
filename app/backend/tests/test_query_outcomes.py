from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.gbif_client import GbifUnavailableError
from services.query_budget import DAILY_LLM_CALL_CAP, try_consume_daily_budget

client = TestClient(app)


def test_resolved_query_returns_a_species_list():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=(
                [{"taxonRank": "class", "taxonValue": "Aves"}],
                {"input_tokens": 10, "output_tokens": 5},
            ),
        ),
        patch("routers.query.resolve_taxon_key", return_value=212),
        patch(
            "routers.query.fetch_top_species",
            return_value=[
                {
                    "species": "Turdus merula",
                    "count": 5,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.41,
                    "hotspot_lon": -3.68,
                }
            ],
        ),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "resolved"
    assert body["taxonFilters"] == [{"taxonRank": "class", "taxonValue": "Aves"}]
    assert body["unresolvedGroups"] == []
    assert body["species"][0]["species"] == "Turdus merula"
    mock_log.assert_called_once_with(
        "birds", "resolved", gbif_result_count=1, input_tokens=10, output_tokens=5
    )


def test_resolved_query_with_multiple_taxon_filters_merges_species():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=(
                [
                    {"taxonRank": "class", "taxonValue": "Aves"},
                    {"taxonRank": "kingdom", "taxonValue": "Plantae"},
                ],
                {},
            ),
        ),
        patch("routers.query.resolve_taxon_key", side_effect=[212, 6]),
        patch(
            "routers.query.fetch_top_species",
            return_value=[
                {
                    "species": "Turdus merula",
                    "count": 5,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.41,
                    "hotspot_lon": -3.68,
                },
                {
                    "species": "Quercus ilex",
                    "count": 3,
                    "kingdom": "Plantae",
                    "hotspot_lat": 40.42,
                    "hotspot_lon": -3.69,
                },
            ],
        ),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post(
            "/api/query", json={"query": "birds and plants", "distinctId": "anon-1"}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "resolved"
    assert body["taxonFilters"] == [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
    ]
    assert body["unresolvedGroups"] == []
    assert [s["species"] for s in body["species"]] == ["Turdus merula", "Quercus ilex"]
    mock_log.assert_called_once_with("birds and plants", "resolved", gbif_result_count=2)


def test_no_taxonomic_signal_returns_unresolved_with_no_gbif_call():
    with (
        patch("routers.query._resolve_taxon_filters", return_value=([], {})),
        patch("routers.query.fetch_top_species") as mock_gbif,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "surprise me", "distinctId": "anon-1"})

    assert response.status_code == 200
    assert response.json()["status"] == "unresolved"
    mock_gbif.assert_not_called()
    mock_log.assert_called_once_with("surprise me", "unresolved")


def test_unresolvable_taxon_returns_unresolved_with_no_gbif_call():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Nonsenseia"}], {}),
        ),
        patch("routers.query.resolve_taxon_key", return_value=None),
        patch("routers.query.fetch_top_species") as mock_gbif,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "gibberish", "distinctId": "anon-1"})

    assert response.status_code == 200
    assert response.json()["status"] == "unresolved"
    mock_gbif.assert_not_called()
    mock_log.assert_called_once_with("gibberish", "unresolved")


def test_resolved_taxon_with_zero_occurrences_returns_no_results():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch("routers.query.resolve_taxon_key", return_value=212),
        patch("routers.query.fetch_top_species", return_value=[]),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "no_results"
    assert body["taxonFilters"] == [{"taxonRank": "class", "taxonValue": "Aves"}]
    mock_log.assert_called_once_with("birds", "no_results", gbif_result_count=0)


def test_gbif_failure_after_retries_returns_502():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch("routers.query.resolve_taxon_key", return_value=212),
        patch("routers.query.fetch_top_species", side_effect=GbifUnavailableError()),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    assert response.status_code == 502
    assert response.json()["status"] == "gbif_unavailable"
    mock_log.assert_called_once_with("birds", "gbif_unavailable")


def test_daily_budget_exhausted_returns_429_with_no_llm_call():
    for _ in range(DAILY_LLM_CALL_CAP):
        try_consume_daily_budget(date.today())

    with (
        patch("routers.query._resolve_taxon_filters") as mock_llm,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    assert response.status_code == 429
    assert response.json()["error"] == "daily_limit_reached"
    mock_llm.assert_not_called()
    mock_log.assert_called_once_with("birds", "daily_limit_reached", guardrail="daily_limit")
