from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.gbif_client import GbifUnavailableError
from services.taxon_resolution import resolve_taxon_key


def test_exact_match_resolves_to_the_ranks_numeric_key():
    with patch("services.taxon_resolution._fetch_species_match") as mock_fetch:
        mock_fetch.return_value = {"matchType": "EXACT", "classKey": 212}

        key = resolve_taxon_key("class", "Aves")

    assert key == 212


def test_fuzzy_match_at_or_above_confidence_threshold_resolves():
    with patch("services.taxon_resolution._fetch_species_match") as mock_fetch:
        mock_fetch.return_value = {
            "matchType": "FUZZY",
            "confidence": 85,
            "classKey": 212,
        }

        key = resolve_taxon_key("class", "Avess")

    assert key == 212


def test_fuzzy_match_below_confidence_threshold_is_unresolved():
    with patch("services.taxon_resolution._fetch_species_match") as mock_fetch:
        mock_fetch.return_value = {
            "matchType": "FUZZY",
            "confidence": 84,
            "classKey": 212,
        }

        key = resolve_taxon_key("class", "Birdz")

    assert key is None


def test_no_match_is_unresolved():
    with patch("services.taxon_resolution._fetch_species_match") as mock_fetch:
        mock_fetch.return_value = {"matchType": "NONE"}

        key = resolve_taxon_key("class", "Nonsenseia")

    assert key is None


def test_a_failing_species_match_request_raises_gbif_unavailable():
    # A slow or failing GBIF species/match is the same class of problem as a
    # failing occurrence/search — it must surface as GBIF-unavailable so the
    # router returns a clear "it's GBIF, not us" response, not a raw 500.
    with (
        patch(
            "services.taxon_resolution.httpx.get",
            side_effect=httpx.ReadTimeout("gbif is slow"),
        ),
        pytest.raises(GbifUnavailableError),
    ):
        resolve_taxon_key("class", "Aves")


def test_queries_gbif_species_match_with_the_name_and_uppercase_rank():
    mock_response = MagicMock()
    mock_response.json.return_value = {"matchType": "EXACT", "classKey": 212}
    with patch("services.taxon_resolution.httpx.get", return_value=mock_response) as mock_get:
        resolve_taxon_key("class", "Aves")

    mock_get.assert_called_once_with(
        "https://api.gbif.org/v1/species/match",
        params={"name": "Aves", "rank": "CLASS"},
        timeout=5.0,
    )
