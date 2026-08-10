import threading
import time
from unittest.mock import patch

import pytest

from services.gbif_client import (
    FALLBACK_YEAR,
    GBIF_POLYGON,
    MAX_RETRIES,
    SCALE_GUARD_THRESHOLD,
    YEAR_RANGE,
    GbifUnavailableError,
    _gbif_search,
    fetch_top_species,
    polygon_centroid,
)


def _occurrence(species, lat, lon, kingdom="Animalia", species_key=None):
    return {
        "species": species,
        "decimalLatitude": lat,
        "decimalLongitude": lon,
        "kingdom": kingdom,
        "speciesKey": species_key,
    }


def test_merges_species_across_multiple_taxon_filters_by_quota_then_sorts_by_count():
    birds = [
        _occurrence("Turdus merula", 40.41, -3.68),
        _occurrence("Turdus merula", 40.41, -3.68),
        _occurrence("Turdus merula", 40.41, -3.68),
        _occurrence("Passer domesticus", 40.42, -3.69),
        _occurrence("Passer domesticus", 40.42, -3.69),
        _occurrence("Corvus monedula", 40.43, -3.70),
    ]
    plants = [
        _occurrence("Quercus ilex", 40.41, -3.68, kingdom="Plantae"),
        _occurrence("Quercus ilex", 40.41, -3.68, kingdom="Plantae"),
        _occurrence("Pinus pinea", 40.42, -3.69, kingdom="Plantae"),
    ]

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": 1}
        if "classKey" in params:
            return {"results": birds, "endOfRecords": True}
        return {"results": plants, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species(
            [
                {"taxon_rank": "class", "taxon_key": 212},
                {"taxon_rank": "kingdom", "taxon_key": 6},
            ]
        )

    # Quota selects Turdus/Passer/Corvus (birds) + Quercus/Pinus (plants) for
    # fairness, but the final list is sorted by count descending, not by
    # group. Passer and Quercus tie at count 2 — stable sort keeps Passer
    # (which came first pre-sort) ahead of Quercus.
    assert [s["species"] for s in species_list] == [
        "Turdus merula",
        "Passer domesticus",
        "Quercus ilex",
        "Corvus monedula",
        "Pinus pinea",
    ]
    assert [s["count"] for s in species_list] == [3, 2, 2, 1, 1]


def test_sorts_the_final_merged_selection_by_observation_count_descending():
    # Reproduces the reported bug: a low-count species from the first-
    # mentioned group (e.g. a lizard, quota-selected for fairness) was
    # appearing ahead of a much-higher-count species from a later group
    # (e.g. a turtle), because groups were concatenated in mention order
    # without a final re-sort by count.
    lizards = [_occurrence("Podarcis virescens", 40.41, -3.68)]
    turtles = [_occurrence("Trachemys scripta", 40.42, -3.69)] * 121 + [
        _occurrence("Graptemys pseudogeographica", 40.43, -3.70)
    ] * 16

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": 1}
        if "orderKey" in params and params["orderKey"] == 1:
            return {"results": lizards, "endOfRecords": True}
        return {"results": turtles, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species(
            [
                {"taxon_rank": "order", "taxon_key": 1},
                {"taxon_rank": "order", "taxon_key": 2},
            ]
        )

    assert [s["species"] for s in species_list] == [
        "Trachemys scripta",
        "Graptemys pseudogeographica",
        "Podarcis virescens",
    ]
    assert [s["count"] for s in species_list] == [121, 16, 1]


def test_polygon_centroid_averages_the_polygon_vertices():
    # A simple closed square: (0,0),(0,2),(2,2),(2,0),(0,0) in "lon lat" WKT
    # order. Average of the 4 unique vertices (excluding the closing repeat
    # of the first point) is (1, 1).
    square = "POLYGON((0 0,0 2,2 2,2 0,0 0))"

    lat, lon = polygon_centroid(square)

    assert lat == pytest.approx(1.0)
    assert lon == pytest.approx(1.0)


def test_polygon_centroid_of_the_default_retiro_polygon():
    lat, lon = polygon_centroid(GBIF_POLYGON)

    assert lat == pytest.approx(40.4137, abs=0.001)
    assert lon == pytest.approx(-3.6826, abs=0.001)


def test_fetches_multiple_taxon_filters_concurrently_not_sequentially():
    per_group_delay = 0.2

    def fake_search(params):
        time.sleep(per_group_delay)
        if params.get("limit") == 0:
            return {"count": 0}
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        start = time.monotonic()
        fetch_top_species(
            [
                {"taxon_rank": "class", "taxon_key": 212},
                {"taxon_rank": "kingdom", "taxon_key": 6},
                {"taxon_rank": "order", "taxon_key": 1},
            ]
        )
        elapsed = time.monotonic() - start

    # 3 groups x 2 sequential GBIF calls each (probe + page) = 6 x 0.2s = 1.2s
    # if sequential. Comfortably under that if groups run concurrently.
    assert elapsed < 0.6


def test_caps_concurrent_gbif_requests_at_three_even_with_more_groups():
    lock = threading.Lock()
    concurrent_count = 0
    max_concurrent_seen = 0

    def fake_search(params):
        nonlocal concurrent_count, max_concurrent_seen
        with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        time.sleep(0.1)
        with lock:
            concurrent_count -= 1
        if params.get("limit") == 0:
            return {"count": 0}
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        fetch_top_species(
            [{"taxon_rank": "class", "taxon_key": i} for i in range(6)]
        )

    assert max_concurrent_seen <= 3


def test_preserves_input_order_even_when_the_first_group_finishes_last():
    # First filter's fake GBIF calls are slowest, last filter's are fastest —
    # completion order is the reverse of input order. If the implementation
    # used something like as_completed() instead of executor.map(), this
    # would catch it: the result list would come back in completion order
    # (key=1 group, then key=6, then key=212) instead of input order.
    delay_by_key = {212: 0.15, 6: 0.08, 1: 0.0}
    species_by_key = {212: "Turdus merula", 6: "Quercus ilex", 1: "Perca fluviatilis"}

    def fake_search(params):
        key = params.get("classKey") or params.get("kingdomKey") or params.get("orderKey")
        time.sleep(delay_by_key[key])
        if params.get("limit") == 0:
            return {"count": 1}
        return {"results": [_occurrence(species_by_key[key], 40.41, -3.68)], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species(
            [
                {"taxon_rank": "class", "taxon_key": 212},
                {"taxon_rank": "kingdom", "taxon_key": 6},
                {"taxon_rank": "order", "taxon_key": 1},
            ]
        )

    assert [s["species"] for s in species_list] == [
        "Turdus merula",
        "Quercus ilex",
        "Perca fluviatilis",
    ]


def test_includes_the_gbif_species_key_for_each_species():
    occurrences = [_occurrence("Turdus merula", 40.41, -3.68, species_key=2495414)] * 3

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": len(occurrences)}
        return {"results": occurrences, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert species_list[0]["species_key"] == 2495414


def test_hotspot_uses_the_density_cluster_not_the_plain_average():
    # A dense cluster of 3 close-together points plus one far outlier. The
    # plain average would be pulled toward the outlier; the density-cluster
    # hotspot should land in the winning grid cell (the dense group) instead.
    dense_points = [
        (40.4100, -3.6800),
        (40.4101, -3.6801),
        (40.4099, -3.6799),
    ]
    outlier = (40.4200, -3.6700)
    occurrences = [_occurrence("Turdus merula", lat, lon) for lat, lon in dense_points + [outlier]]

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": len(occurrences)}
        return {"results": occurrences, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    species = species_list[0]
    plain_avg_lat = sum(lat for lat, _ in dense_points + [outlier]) / 4
    plain_avg_lon = sum(lon for _, lon in dense_points + [outlier]) / 4

    assert abs(species["hotspot_lat"] - plain_avg_lat) > 0.001
    assert abs(species["hotspot_lon"] - plain_avg_lon) > 0.001
    assert abs(species["hotspot_lat"] - 40.41) < 0.001
    assert abs(species["hotspot_lon"] - (-3.68)) < 0.001


def test_clustering_diagnostics_when_a_winning_cell_is_found():
    dense_points = [
        (40.4100, -3.6800),
        (40.4101, -3.6801),
        (40.4099, -3.6799),
    ]
    outlier = (40.4200, -3.6700)
    occurrences = [_occurrence("Turdus merula", lat, lon) for lat, lon in dense_points + [outlier]]

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": len(occurrences)}
        return {"results": occurrences, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    clustering = species_list[0]["clustering"]
    assert clustering["fallback_reason"] is None
    assert clustering["winning_cell_count"] == 3
    assert clustering["cells_occupied"] >= 1
    assert clustering["distance_from_average_m"] > 0


def test_clustering_diagnostics_fall_back_below_min_points():
    occurrences = [
        _occurrence("Turdus merula", 40.41, -3.68),
        _occurrence("Turdus merula", 40.42, -3.69),
    ]

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": len(occurrences)}
        return {"results": occurrences, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    clustering = species_list[0]["clustering"]
    assert clustering["fallback_reason"] == "too_few_points"
    assert clustering["cells_occupied"] is None
    assert clustering["winning_cell_count"] is None
    assert clustering["distance_from_average_m"] == 0.0


def test_defaults_to_the_retiro_park_polygon():
    seen_geometries = []

    def fake_search(params):
        seen_geometries.append(params.get("geometry"))
        if params.get("limit") == 0:
            return {"count": 0}
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert seen_geometries and all(g == GBIF_POLYGON for g in seen_geometries)


def test_uses_a_provided_polygon_instead_of_the_default():
    custom_polygon = "POLYGON((0 0,0 1,1 1,1 0,0 0))"
    seen_geometries = []

    def fake_search(params):
        seen_geometries.append(params.get("geometry"))
        if params.get("limit") == 0:
            return {"count": 0}
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}], polygon=custom_polygon)

    assert seen_geometries and all(g == custom_polygon for g in seen_geometries)


def test_ranks_species_with_the_most_observed_first():
    occurrences = [_occurrence("Turdus merula", 40.41, -3.68)] * 3 + [
        _occurrence("Passer domesticus", 40.42, -3.69)
    ] * 5

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": len(occurrences)}
        return {"results": occurrences, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert [s["species"] for s in species_list] == ["Passer domesticus", "Turdus merula"]
    assert species_list[0]["count"] == 5
    assert species_list[0]["kingdom"] == "Animalia"


def test_falls_back_to_a_single_year_when_the_scale_guard_threshold_is_exceeded():
    fetch_years = []

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": SCALE_GUARD_THRESHOLD + 1}
        fetch_years.append(params["year"])
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert fetch_years == [FALLBACK_YEAR]


def test_uses_the_full_year_range_when_under_the_scale_guard_threshold():
    fetch_years = []

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": SCALE_GUARD_THRESHOLD - 1}
        fetch_years.append(params["year"])
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert fetch_years == [YEAR_RANGE]


def test_combines_occurrences_across_multiple_pages():
    page_one = [_occurrence("Turdus merula", 40.41, -3.68)]
    page_two = [_occurrence("Turdus merula", 40.42, -3.69)]

    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": 2}
        if params["offset"] == 0:
            return {"results": page_one, "endOfRecords": False}
        return {"results": page_two, "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert species_list[0]["count"] == 2


def test_returns_no_species_when_gbif_has_zero_occurrences():
    def fake_search(params):
        if params.get("limit") == 0:
            return {"count": 0}
        return {"results": [], "endOfRecords": True}

    with patch("services.gbif_client._gbif_search", side_effect=fake_search):
        species_list = fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])

    assert species_list == []


def test_raises_when_gbif_fails_after_all_retries():
    with (
        patch("services.gbif_client._request_occurrence_page", return_value=None),
        pytest.raises(GbifUnavailableError),
    ):
        fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])


def test_retries_the_configured_number_of_times_before_giving_up():
    with patch(
        "services.gbif_client._request_occurrence_page", return_value=None
    ) as mock_request, pytest.raises(GbifUnavailableError):
        _gbif_search({"limit": 0})

    assert mock_request.call_count == MAX_RETRIES + 1
