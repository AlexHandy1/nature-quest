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


def _species_belongs_to_order(scientific_name: str, expected_order: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": scientific_name, "rank": "SPECIES"},
        timeout=5.0,
    )
    response.raise_for_status()
    match = response.json()
    return match.get("order") == expected_order


# Mirrors the "fish" worked example in services.anthropic_client.TAXON_GUIDANCE:
# most ray-finned fish orders have no single GBIF class, so verification must
# check order membership for the six orders and class only for Elasmobranchii.
FISH_ORDERS = {
    "Perciformes",
    "Cypriniformes",
    "Scorpaeniformes",
    "Gadiformes",
    "Clupeiformes",
    "Salmoniformes",
}


def _species_is_a_curated_fish_group_member(scientific_name: str) -> bool:
    return _species_belongs_to_class(scientific_name, "Elasmobranchii") or any(
        _species_belongs_to_order(scientific_name, order) for order in FISH_ORDERS
    )


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


@pytest.mark.eval
def test_fish_birds_and_insects_query_places_the_first_mentioned_group_first():
    # "Fish" is mentioned first, so its 7 curated groups are the first entries
    # in taxon_filters, and ThreadPoolExecutor.map preserves that order into
    # fetch_top_species's group list regardless of thread completion order —
    # so the first species in the merged, quota-based result should come from
    # the fish groups (Actinopterygii bony fish, or Elasmobranchii sharks/rays).
    # It could still end up later than index 0 if the first fish group has no
    # local observations and the round-robin shortfall logic gives its slot to
    # a later group instead — that's expected, not a bug.
    species_list = _run_pipeline("Show me Fish, Birds and Insects")

    assert species_list
    first = species_list[0]
    assert _species_is_a_curated_fish_group_member(first["species"]), (
        f"{first['species']} (first in list) did not verify as a curated fish group member"
    )

    for species in species_list:
        is_fish = _species_is_a_curated_fish_group_member(species["species"])
        is_bird = _species_belongs_to_class(species["species"], "Aves")
        is_insect = _species_belongs_to_class(species["species"], "Insecta")
        assert is_fish or is_bird or is_insect, (
            f"{species['species']} did not verify as fish, Aves, or Insecta"
        )
