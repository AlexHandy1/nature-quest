import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from routers.query import _resolve_taxon_keys
from services.gbif_client import GbifUnavailableError
from services.query_budget import DAILY_LLM_CALL_CAP, try_consume_daily_budget

client = TestClient(app)


def test_resolve_taxon_keys_resolves_multiple_filters_concurrently_not_sequentially():
    per_filter_delay = 0.2
    taxon_filters = [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        {"taxonRank": "order", "taxonValue": "Perciformes"},
    ]

    def fake_resolve(rank, value):
        time.sleep(per_filter_delay)
        return 1

    with patch("routers.query.resolve_taxon_key", side_effect=fake_resolve):
        start = time.monotonic()
        _resolve_taxon_keys(taxon_filters)
        elapsed = time.monotonic() - start

    assert elapsed < 0.4


def test_resolve_taxon_keys_caps_concurrent_gbif_requests_at_three():
    lock = threading.Lock()
    concurrent_count = 0
    max_concurrent_seen = 0
    taxon_filters = [
        {"taxonRank": "class", "taxonValue": f"Group{i}"} for i in range(6)
    ]

    def fake_resolve(rank, value):
        nonlocal concurrent_count, max_concurrent_seen
        with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        time.sleep(0.1)
        with lock:
            concurrent_count -= 1
        return 1

    with patch("routers.query.resolve_taxon_key", side_effect=fake_resolve):
        _resolve_taxon_keys(taxon_filters)

    assert max_concurrent_seen <= 3


def test_resolve_taxon_keys_preserves_input_order_even_when_the_first_filter_finishes_last():
    delay_by_value = {"Aves": 0.15, "Plantae": 0.08, "Perciformes": 0.0}
    key_by_value = {"Aves": 212, "Plantae": 6, "Perciformes": 1}
    taxon_filters = [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        {"taxonRank": "order", "taxonValue": "Perciformes"},
    ]

    def fake_resolve(rank, value):
        time.sleep(delay_by_value[value])
        return key_by_value[value]

    with patch("routers.query.resolve_taxon_key", side_effect=fake_resolve):
        resolved, unresolved_groups = _resolve_taxon_keys(taxon_filters)

    assert [r["taxonValue"] for r in resolved] == ["Aves", "Plantae", "Perciformes"]
    assert [r["taxon_key"] for r in resolved] == [212, 6, 1]
    assert unresolved_groups == []


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
        "birds",
        "resolved",
        distinct_id="anon-1",
        gbif_result_count=1,
        input_tokens=10,
        output_tokens=5,
    )


def test_resolved_species_are_returned_in_nearest_neighbour_route_order():
    # Center is Retiro's fixed CENTER_LAT/CENTER_LON (40.4153, -3.6844).
    # "Far" is mentioned/returned first by fetch_top_species (count order),
    # but "Near" is much closer to the center, so route order should place
    # Near first despite count order saying otherwise.
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch("routers.query.resolve_taxon_key", return_value=212),
        patch(
            "routers.query.fetch_top_species",
            return_value=[
                {
                    "species": "Far",
                    "count": 10,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.50,
                    "hotspot_lon": -3.60,
                },
                {
                    "species": "Near",
                    "count": 1,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.4154,
                    "hotspot_lon": -3.6845,
                },
            ],
        ),
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    body = response.json()
    assert [s["species"] for s in body["species"]] == ["Near", "Far"]
    assert body["species"][0]["distance_m"] < body["species"][1]["distance_m"]


def test_resolved_response_omits_internal_clustering_diagnostics():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
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
                    "clustering": {
                        "cells_occupied": 4,
                        "winning_cell_count": 3,
                        "fallback_reason": None,
                        "distance_from_average_m": 12.3,
                    },
                }
            ],
        ),
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    body = response.json()
    assert "clustering" not in body["species"][0]


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
    mock_log.assert_called_once_with(
        "birds and plants", "resolved", distinct_id="anon-1", gbif_result_count=2
    )


def test_more_than_ten_taxon_filters_caps_gbif_calls_and_marks_the_rest_unresolved():
    taxon_filters = [
        {"taxonRank": "class", "taxonValue": f"Group{i}"} for i in range(11)
    ]

    with (
        patch(
            "routers.query._resolve_taxon_filters", return_value=(taxon_filters, {})
        ),
        patch("routers.query.resolve_taxon_key", side_effect=range(10)),
        patch("routers.query.fetch_top_species", return_value=[]),
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post(
            "/api/query", json={"query": "eleven groups", "distinctId": "anon-1"}
        )

    body = response.json()
    assert response.status_code == 200
    assert len(body["taxonFilters"]) == 10
    assert body["unresolvedGroups"] == ["Group10"]


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
    mock_log.assert_called_once_with("surprise me", "unresolved", distinct_id="anon-1")


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
    mock_log.assert_called_once_with("gibberish", "unresolved", distinct_id="anon-1")


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
    mock_log.assert_called_once_with(
        "birds", "no_results", distinct_id="anon-1", gbif_result_count=0
    )


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
    mock_log.assert_called_once_with("birds", "gbif_unavailable", distinct_id="anon-1")


def test_daily_budget_exhausted_returns_429_with_no_llm_call():
    for _ in range(DAILY_LLM_CALL_CAP):
        try_consume_daily_budget(datetime.now(tz=timezone.utc).date())

    with (
        patch("routers.query._resolve_taxon_filters") as mock_llm,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post("/api/query", json={"query": "birds", "distinctId": "anon-1"})

    assert response.status_code == 429
    assert response.json()["error"] == "daily_limit_reached"
    mock_llm.assert_not_called()
    mock_log.assert_called_once_with(
        "birds", "daily_limit_reached", distinct_id="anon-1", guardrail="daily_limit"
    )
