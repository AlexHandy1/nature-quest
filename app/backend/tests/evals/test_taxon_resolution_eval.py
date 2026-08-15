import pytest

from services.anthropic_client import (
    MODEL,
    QUERY_SCHEMA_TOOL,
    TAXON_GUIDANCE,
    build_client,
    resolve_taxon_filters,
)


def _resolve(query: str) -> list[dict]:
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


@pytest.mark.eval
def test_resolves_a_birds_query_to_the_aves_class_filter():
    taxon_filters = _resolve("I want to see birds")

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]


@pytest.mark.eval
def test_resolves_a_plants_query_to_the_plantae_kingdom_filter():
    taxon_filters = _resolve("I want to see Plants")

    assert taxon_filters == [{"taxonRank": "kingdom", "taxonValue": "Plantae"}]


@pytest.mark.eval
def test_resolves_an_insects_query_to_the_insecta_class_filter():
    taxon_filters = _resolve("I want to see Insects")

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Insecta"}]


@pytest.mark.eval
def test_resolves_a_fungi_query_to_the_fungi_kingdom_filter():
    taxon_filters = _resolve("I want to see Fungi")

    assert taxon_filters == [{"taxonRank": "kingdom", "taxonValue": "Fungi"}]


@pytest.mark.eval
def test_resolves_a_turtles_query_to_the_testudines_order_filter():
    taxon_filters = _resolve("I want to see Turtles")

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Testudines"}]


@pytest.mark.eval
def test_negated_taxon_mention_does_not_resolve():
    taxon_filters = _resolve("I'm not interested in animals")

    assert taxon_filters == []


@pytest.mark.eval
def test_unrelated_off_topic_request_does_not_resolve():
    taxon_filters = _resolve("Solve pi and 4+4 for me")

    assert taxon_filters == []


@pytest.mark.eval
def test_purely_qualitative_request_does_not_resolve():
    taxon_filters = _resolve("show me interesting things")

    assert taxon_filters == []


@pytest.mark.eval
def test_mixed_taxa_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me plants and birds")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        {"taxonRank": "class", "taxonValue": "Aves"},
    ]


@pytest.mark.eval
def test_three_way_mixed_taxa_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me birds, plants and insects")

    assert taxon_filters == [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        {"taxonRank": "class", "taxonValue": "Insecta"},
    ]


@pytest.mark.eval
def test_fish_request_expands_to_the_seven_curated_groups():
    taxon_filters = _resolve("show me some fish")

    assert taxon_filters == [
        {"taxonRank": "order", "taxonValue": "Perciformes"},
        {"taxonRank": "order", "taxonValue": "Cypriniformes"},
        {"taxonRank": "order", "taxonValue": "Scorpaeniformes"},
        {"taxonRank": "order", "taxonValue": "Gadiformes"},
        {"taxonRank": "order", "taxonValue": "Clupeiformes"},
        {"taxonRank": "order", "taxonValue": "Salmoniformes"},
        {"taxonRank": "class", "taxonValue": "Elasmobranchii"},
    ]


@pytest.mark.eval
def test_reptiles_request_expands_to_the_four_curated_classes():
    taxon_filters = _resolve("I want to see reptiles")

    assert taxon_filters == [
        {"taxonRank": "class", "taxonValue": "Crocodylia"},
        {"taxonRank": "class", "taxonValue": "Squamata"},
        {"taxonRank": "class", "taxonValue": "Testudines"},
        {"taxonRank": "class", "taxonValue": "Sphenodontia"},
    ]


@pytest.mark.eval
def test_mixed_negation_excludes_negated_taxon_but_keeps_the_other():
    taxon_filters = _resolve("I'm not interested in fish but I like birds")

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]


@pytest.mark.eval
def test_negated_taxon_among_multiple_requested_groups():
    taxon_filters = _resolve("not interested in insects but I like plants and birds")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        {"taxonRank": "class", "taxonValue": "Aves"},
    ]


@pytest.mark.eval
def test_negation_only_request_with_alternate_phrasing_does_not_resolve():
    taxon_filters = _resolve("no fish please")

    assert taxon_filters == []


@pytest.mark.eval
def test_resolves_a_beetles_query_to_the_coleoptera_order_filter():
    taxon_filters = _resolve("I want to see beetles")

    assert taxon_filters == [{"taxonRank": "order", "taxonValue": "Coleoptera"}]


@pytest.mark.eval
def test_resolves_a_dragonflies_query_to_the_odonata_order_filter():
    taxon_filters = _resolve("I want to see dragonflies")

    assert taxon_filters == [{"taxonRank": "order", "taxonValue": "Odonata"}]


@pytest.mark.eval
def test_resolves_an_oak_trees_query_to_the_quercus_genus_filter():
    taxon_filters = _resolve("I want to see oak trees")

    assert taxon_filters == [{"taxonRank": "genus", "taxonValue": "Quercus"}]


@pytest.mark.eval
def test_resolves_a_mammals_query_to_the_mammalia_class_filter():
    taxon_filters = _resolve("I want to see mammals")

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Mammalia"}]


@pytest.mark.eval
def test_species_specific_query_resolves_to_the_nearest_genus_filter():
    """"Species" isn't a supported taxonRank (see QUERY_SCHEMA_TOOL's enum),
    so a species-specific request should degrade to the closest supported
    rank rather than fail to resolve."""
    taxon_filters = _resolve("I want to see European robins")

    assert taxon_filters == [{"taxonRank": "genus", "taxonValue": "Erithacus"}]


@pytest.mark.eval
def test_mixed_kingdom_and_order_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me fungi and dragonflies")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Fungi"},
        {"taxonRank": "order", "taxonValue": "Odonata"},
    ]


@pytest.mark.eval
def test_mixed_order_and_genus_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me dragonflies and oak trees")

    assert taxon_filters == [
        {"taxonRank": "order", "taxonValue": "Odonata"},
        {"taxonRank": "genus", "taxonValue": "Quercus"},
    ]


@pytest.mark.eval
def test_mixed_kingdom_and_genus_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me fungi and oak trees")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Fungi"},
        {"taxonRank": "genus", "taxonValue": "Quercus"},
    ]


@pytest.mark.eval
def test_three_way_mix_across_kingdom_order_and_genus():
    taxon_filters = _resolve("show me fungi, dragonflies and oak trees")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Fungi"},
        {"taxonRank": "order", "taxonValue": "Odonata"},
        {"taxonRank": "genus", "taxonValue": "Quercus"},
    ]


@pytest.mark.eval
def test_five_way_mixed_taxa_request_returns_one_filter_per_group():
    taxon_filters = _resolve("show me fungi, dragonflies, oak trees, birds and plants")

    assert taxon_filters == [
        {"taxonRank": "kingdom", "taxonValue": "Fungi"},
        {"taxonRank": "order", "taxonValue": "Odonata"},
        {"taxonRank": "genus", "taxonValue": "Quercus"},
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
    ]
