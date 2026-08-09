import httpx
import pytest

from services.anthropic_client import (
    MODEL,
    QUERY_SCHEMA_TOOL,
    TAXON_GUIDANCE,
    build_client,
    resolve_taxon_filters,
)
from routers.query import _resolve_taxon_keys
from services.gbif_client import GBIF_POLYGON, fetch_top_species
from services.taxon_resolution import GBIF_SPECIES_MATCH_URL


def _resolve_filters(query: str) -> list[dict]:
    taxon_filters = resolve_taxon_filters(query, build_client())
    print(
        f"[eval] resolve_taxon_filters\n"
        f"  model={MODEL!r}\n"
        f"  system={TAXON_GUIDANCE!r}\n"
        f"  tools={[QUERY_SCHEMA_TOOL]!r}\n"
        f"  input={query!r}\n"
        f"  output={taxon_filters!r}"
    )
    return taxon_filters


def _fetch_species(resolved_filters: list[dict]) -> list[dict]:
    species_list = fetch_top_species(resolved_filters, polygon=GBIF_POLYGON)
    print(f"[eval] fetch_top_species input={resolved_filters!r} output={species_list!r}")
    return species_list


def _run_pipeline(query: str) -> list[dict]:
    taxon_filters = _resolve_filters(query)
    resolved, unresolved_groups = _resolve_taxon_keys(taxon_filters)
    print(f"[eval] _resolve_taxon_keys resolved={resolved!r} unresolved_groups={unresolved_groups!r}")
    resolved_filters = [
        {"taxon_rank": r["taxonRank"], "taxon_key": r["taxon_key"]} for r in resolved
    ]
    return _fetch_species(resolved_filters)


def _species_belongs_to_class(scientific_name: str, expected_class: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": scientific_name, "rank": "SPECIES"},
        timeout=5.0,
    )
    response.raise_for_status()
    match = response.json()
    return match.get("class") == expected_class


def _species_belongs_to_kingdom(scientific_name: str, expected_kingdom: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": scientific_name, "rank": "SPECIES"},
        timeout=5.0,
    )
    response.raise_for_status()
    match = response.json()
    return match.get("kingdom") == expected_kingdom


@pytest.mark.eval
def test_birds_query_returns_a_non_empty_list_of_genuinely_aves_species():
    species_list = _run_pipeline("I want to see birds")

    assert species_list
    for species in species_list:
        assert _species_belongs_to_class(species["species"], "Aves"), (
            f"{species['species']} did not verify as class Aves via species/match"
        )


@pytest.mark.eval
def test_birds_and_plants_query_returns_a_genuine_mix_of_both_groups():
    species_list = _run_pipeline("I want to see birds and plants")

    assert species_list
    for species in species_list:
        is_bird = _species_belongs_to_class(species["species"], "Aves")
        is_plant = _species_belongs_to_kingdom(species["species"], "Plantae")
        assert is_bird or is_plant, (
            f"{species['species']} did not verify as class Aves or kingdom Plantae"
        )
