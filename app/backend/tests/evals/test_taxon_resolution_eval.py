import pytest

from services.anthropic_client import (
    MODEL,
    QUERY_SCHEMA_TOOL,
    TAXON_GUIDANCE,
    build_client,
    resolve_taxon_filter,
)


def _resolve(query: str) -> dict | None:
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


@pytest.mark.eval
def test_resolves_a_birds_query_to_the_aves_class_filter():
    taxon_filter = _resolve("I want to see birds")

    assert taxon_filter == {"taxonRank": "class", "taxonValue": "Aves"}


@pytest.mark.eval
def test_resolves_a_plants_query_to_the_plantae_kingdom_filter():
    taxon_filter = _resolve("I want to see Plants")

    assert taxon_filter == {"taxonRank": "kingdom", "taxonValue": "Plantae"}


@pytest.mark.eval
def test_resolves_an_insects_query_to_the_insecta_class_filter():
    taxon_filter = _resolve("I want to see Insects")

    assert taxon_filter == {"taxonRank": "class", "taxonValue": "Insecta"}


@pytest.mark.eval
def test_resolves_a_fungi_query_to_the_fungi_kingdom_filter():
    taxon_filter = _resolve("I want to see Fungi")

    assert taxon_filter == {"taxonRank": "kingdom", "taxonValue": "Fungi"}


@pytest.mark.eval
def test_resolves_a_turtles_query_to_the_testudines_order_filter():
    taxon_filter = _resolve("I want to see Turtles")

    assert taxon_filter == {"taxonRank": "order", "taxonValue": "Testudines"}


@pytest.mark.eval
def test_negated_taxon_mention_does_not_resolve():
    taxon_filter = _resolve("I'm not interested in animals")

    assert taxon_filter is None


@pytest.mark.eval
def test_unrelated_off_topic_request_does_not_resolve():
    taxon_filter = _resolve("Solve pi and 4+4 for me")

    assert taxon_filter is None


@pytest.mark.eval
def test_purely_qualitative_request_does_not_resolve():
    taxon_filter = _resolve("show me interesting things")

    assert taxon_filter is None


@pytest.mark.eval
def test_mixed_taxa_request_picks_the_first_mentioned_taxon():
    taxon_filter = _resolve("show me plants and birds")

    assert taxon_filter == {"taxonRank": "kingdom", "taxonValue": "Plantae"}
