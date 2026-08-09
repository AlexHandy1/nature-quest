from unittest.mock import patch

import pytest

from services.gbif_client import (
    FALLBACK_YEAR,
    MAX_RETRIES,
    SCALE_GUARD_THRESHOLD,
    YEAR_RANGE,
    GbifUnavailableError,
    _gbif_search,
    fetch_top_species,
)


def _occurrence(species, lat, lon, kingdom="Animalia"):
    return {
        "species": species,
        "decimalLatitude": lat,
        "decimalLongitude": lon,
        "kingdom": kingdom,
    }


def test_merges_species_across_multiple_taxon_filters_by_quota():
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

    assert [s["species"] for s in species_list] == [
        "Turdus merula",
        "Passer domesticus",
        "Corvus monedula",
        "Quercus ilex",
        "Pinus pinea",
    ]


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
    with patch("services.gbif_client._request_occurrence_page", return_value=None):
        with pytest.raises(GbifUnavailableError):
            fetch_top_species([{"taxon_rank": "class", "taxon_key": 212}])


def test_retries_the_configured_number_of_times_before_giving_up():
    with patch(
        "services.gbif_client._request_occurrence_page", return_value=None
    ) as mock_request:
        with pytest.raises(GbifUnavailableError):
            _gbif_search({"limit": 0})

    assert mock_request.call_count == MAX_RETRIES + 1
