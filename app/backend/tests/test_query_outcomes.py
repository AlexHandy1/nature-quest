from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.query_budget import DAILY_LLM_CALL_CAP, try_consume_daily_budget

client = TestClient(app)

RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)


def test_resolved_query_returns_a_species_list():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=(
                [{"taxonRank": "class", "taxonValue": "Aves"}],
                {"input_tokens": 10, "output_tokens": 5},
            ),
        ),
        patch(
            "routers.query.query_species_for_filter",
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
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": RETIRO_POLYGON},
        )

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
    # "Far" is mentioned/returned first by query_species_for_filter (count
    # order), but "Near" is much closer to the center, so route order should
    # place Near first despite count order saying otherwise.
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch(
            "routers.query.query_species_for_filter",
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
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": RETIRO_POLYGON},
        )

    body = response.json()
    assert [s["species"] for s in body["species"]] == ["Near", "Far"]
    assert body["species"][0]["distance_m"] < body["species"][1]["distance_m"]


def test_custom_polygon_is_used_for_route_ordering_around_its_own_centroid():
    # Rascafria-area polygon, centroid far from Retiro's (~40.41, -3.68).
    custom_polygon = (
        "POLYGON((-3.88 40.90,-3.86 40.90,-3.86 40.88,-3.88 40.88,-3.88 40.90))"
    )
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch(
            "routers.query.query_species_for_filter",
            return_value=[
                {
                    "species": "Near custom center",
                    "count": 1,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.89,
                    "hotspot_lon": -3.87,
                },
            ],
        ),
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": custom_polygon},
        )

    assert response.status_code == 200
    body = response.json()
    # Distance from the custom polygon's own centroid should be small (tens of
    # metres), not the ~55km it would be from Retiro's centroid.
    assert body["species"][0]["distance_m"] < 5000


def test_resolved_species_are_enriched_with_common_name_and_image_url():
    def fake_enrich(species_list):
        return [{**s, "image_url": "https://example.com/img.jpg"} for s in species_list]

    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch(
            "routers.query.query_species_for_filter",
            return_value=[
                {
                    "species": "Turdus merula",
                    "common_name": "Common Blackbird",
                    "count": 5,
                    "kingdom": "Animalia",
                    "hotspot_lat": 40.41,
                    "hotspot_lon": -3.68,
                }
            ],
        ),
        patch("routers.query.enrich_species", side_effect=fake_enrich) as mock_enrich,
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": RETIRO_POLYGON},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["species"][0]["common_name"] == "Common Blackbird"
    assert body["species"][0]["image_url"] == "https://example.com/img.jpg"
    mock_enrich.assert_called_once()


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
        patch(
            "routers.query.query_species_for_filter",
            side_effect=[
                [
                    {
                        "species": "Turdus merula",
                        "count": 5,
                        "kingdom": "Animalia",
                        "hotspot_lat": 40.41,
                        "hotspot_lon": -3.68,
                    }
                ],
                [
                    {
                        "species": "Quercus ilex",
                        "count": 3,
                        "kingdom": "Plantae",
                        "hotspot_lat": 40.42,
                        "hotspot_lon": -3.69,
                    }
                ],
            ],
        ),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post(
            "/api/query",
            json={
                "query": "birds and plants",
                "distinctId": "anon-1",
                "polygon": RETIRO_POLYGON,
            },
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


def test_more_than_ten_taxon_filters_caps_duckdb_queries_and_marks_the_rest_unresolved():
    taxon_filters = [
        {"taxonRank": "class", "taxonValue": f"Group{i}"} for i in range(11)
    ]

    with (
        patch("routers.query._resolve_taxon_filters", return_value=(taxon_filters, {})),
        patch("routers.query.query_species_for_filter", return_value=[]),
        patch("routers.query.log_query_outcome"),
    ):
        response = client.post(
            "/api/query",
            json={
                "query": "eleven groups",
                "distinctId": "anon-1",
                "polygon": RETIRO_POLYGON,
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert len(body["taxonFilters"]) == 10
    assert body["unresolvedGroups"] == ["Group10"]


def test_no_taxonomic_signal_returns_unresolved_with_no_duckdb_call():
    with (
        patch("routers.query._resolve_taxon_filters", return_value=([], {})),
        patch("routers.query.query_species_for_filter") as mock_duckdb,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post(
            "/api/query",
            json={
                "query": "surprise me",
                "distinctId": "anon-1",
                "polygon": RETIRO_POLYGON,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "unresolved"
    mock_duckdb.assert_not_called()
    mock_log.assert_called_once_with("surprise me", "unresolved", distinct_id="anon-1")


def test_resolved_taxon_with_zero_occurrences_returns_no_results():
    with (
        patch(
            "routers.query._resolve_taxon_filters",
            return_value=([{"taxonRank": "class", "taxonValue": "Aves"}], {}),
        ),
        patch("routers.query.query_species_for_filter", return_value=[]),
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": RETIRO_POLYGON},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "no_results"
    assert body["taxonFilters"] == [{"taxonRank": "class", "taxonValue": "Aves"}]
    mock_log.assert_called_once_with(
        "birds", "no_results", distinct_id="anon-1", gbif_result_count=0
    )


def test_daily_budget_exhausted_returns_429_with_no_llm_call():
    for _ in range(DAILY_LLM_CALL_CAP):
        try_consume_daily_budget(datetime.now(tz=timezone.utc).date())

    with (
        patch("routers.query._resolve_taxon_filters") as mock_llm,
        patch("routers.query.log_query_outcome") as mock_log,
    ):
        response = client.post(
            "/api/query",
            json={"query": "birds", "distinctId": "anon-1", "polygon": RETIRO_POLYGON},
        )

    assert response.status_code == 429
    assert response.json()["error"] == "daily_limit_reached"
    mock_llm.assert_not_called()
    mock_log.assert_called_once_with(
        "birds", "daily_limit_reached", distinct_id="anon-1", guardrail="daily_limit"
    )
