import httpx
import pytest

from routers.query import _resolve_openrouter_api_key, _resolve_taxon_keys
from services.ai_observability import build_openrouter_client
from services.anthropic_client import TAXON_GUIDANCE
from services.gbif_client import GBIF_POLYGON, fetch_top_species
from services.openrouter_taxon_client import (
    MODEL,
    QUERY_SCHEMA_TOOL_OPENAI as QUERY_SCHEMA_TOOL,
    resolve_taxon_filters,
)
from services.taxon_resolution import GBIF_SPECIES_MATCH_URL


def _build_client():
    return build_openrouter_client(
        consent=False, distinct_id="eval-suite", api_key=_resolve_openrouter_api_key()
    )


def _resolve_filters(query: str) -> list[dict]:
    taxon_filters = resolve_taxon_filters(query, _build_client())
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


# Mirrors the "reptiles" worked example in TAXON_GUIDANCE: no single GBIF
# "Reptilia" class, so it's expanded into four class-rank entries.
REPTILE_CLASSES = {"Crocodylia", "Squamata", "Testudines", "Sphenodontia"}


def _species_is_a_curated_reptile_group_member(scientific_name: str) -> bool:
    return any(
        _species_belongs_to_class(scientific_name, reptile_class)
        for reptile_class in REPTILE_CLASSES
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
def test_fish_birds_and_insects_query_sorts_the_merged_result_by_count_descending():
    # "Fish" expands to 7 curated groups, mentioned before Birds/Insects, so
    # taxon_filters (and therefore fetch_top_species's group list) puts fish
    # groups first. Quota selection picks species per group for fairness
    # across taxa, but the merged, displayed list must still come back sorted
    # by observation count overall — a low-count species from an
    # earlier-mentioned group must not outrank a high-count species from a
    # later one just because of group order.
    species_list = _run_pipeline("Show me Fish, Birds and Insects")

    assert species_list
    counts = [s["count"] for s in species_list]
    assert counts == sorted(counts, reverse=True), (
        f"species list not sorted by count descending: {species_list!r}"
    )

    for species in species_list:
        is_fish = _species_is_a_curated_fish_group_member(species["species"])
        is_bird = _species_belongs_to_class(species["species"], "Aves")
        is_insect = _species_belongs_to_class(species["species"], "Insecta")
        assert is_fish or is_bird or is_insect, (
            f"{species['species']} did not verify as fish, Aves, or Insecta"
        )


@pytest.mark.eval
def test_reptiles_and_fish_query_sorts_the_merged_result_by_count_descending():
    # Reproduces the exact bug report: "reptiles" expands to 4 curated
    # groups and "fish" to 7, for 11 total groups (capped at
    # routers.query.MAX_TAXON_FILTERS = 10, so one group is dropped as
    # unresolved — expected, not asserted on here). Reported symptom was a
    # count-1 lizard appearing ahead of a count-121 turtle because groups
    # were concatenated by mention order without a final sort by count.
    species_list = _run_pipeline("Show me reptiles and fish")

    assert species_list
    counts = [s["count"] for s in species_list]
    assert counts == sorted(counts, reverse=True), (
        f"species list not sorted by count descending: {species_list!r}"
    )

    for species in species_list:
        is_reptile = _species_is_a_curated_reptile_group_member(species["species"])
        is_fish = _species_is_a_curated_fish_group_member(species["species"])
        assert is_reptile or is_fish, (
            f"{species['species']} did not verify as a curated reptile or fish group member"
        )
