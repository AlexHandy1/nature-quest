import httpx
import pytest

from services.anthropic_client import (
    MODEL,
    QUERY_SCHEMA_TOOL,
    TAXON_GUIDANCE,
    build_client,
    resolve_taxon_filter,
)
from services.gbif_client import fetch_top_species
from services.taxon_resolution import GBIF_SPECIES_MATCH_URL, resolve_taxon_key


def _resolve_filter(query: str) -> dict | None:
    taxon_filter = resolve_taxon_filter(query, build_client())
    print(
        f"[eval] resolve_taxon_filter\n"
        f"  model={MODEL!r}\n"
        f"  system={TAXON_GUIDANCE!r}\n"
        f"  tools={[QUERY_SCHEMA_TOOL]!r}\n"
        f"  input={query!r}\n"
        f"  output={taxon_filter!r}"
    )
    return taxon_filter


def _resolve_key(taxon_rank: str, taxon_value: str) -> int | None:
    taxon_key = resolve_taxon_key(taxon_rank, taxon_value)
    print(f"[eval] resolve_taxon_key input=({taxon_rank!r}, {taxon_value!r}) output={taxon_key!r}")
    return taxon_key


def _fetch_species(taxon_rank: str, taxon_key: int) -> list[dict]:
    species_list = fetch_top_species(taxon_rank, taxon_key)
    print(f"[eval] fetch_top_species input=({taxon_rank!r}, {taxon_key!r}) output={species_list!r}")
    return species_list


def _run_pipeline(query: str) -> list[dict]:
    taxon_filter = _resolve_filter(query)
    taxon_key = _resolve_key(taxon_filter["taxonRank"], taxon_filter["taxonValue"])
    return _fetch_species(taxon_filter["taxonRank"], taxon_key)


def _species_belongs_to_class(scientific_name: str, expected_class: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": scientific_name, "rank": "SPECIES"},
        timeout=5.0,
    )
    response.raise_for_status()
    match = response.json()
    return match.get("class") == expected_class


@pytest.mark.eval
def test_birds_query_returns_a_non_empty_list_of_genuinely_aves_species():
    species_list = _run_pipeline("I want to see birds")

    assert species_list
    for species in species_list:
        assert _species_belongs_to_class(species["species"], "Aves"), (
            f"{species['species']} did not verify as class Aves via species/match"
        )
