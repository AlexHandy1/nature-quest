import pytest

from services.anthropic_client import build_client, resolve_taxon_filter


@pytest.mark.eval
def test_resolves_a_birds_query_to_the_aves_class_filter():
    client = build_client()

    taxon_filter = resolve_taxon_filter("I want to see birds", client)

    assert taxon_filter == {"taxonRank": "class", "taxonValue": "Aves"}

@pytest.mark.eval
def test_resolves_a_plants_query_to_the_plantae_kingdom_filter():
    client = build_client()

    taxon_filter = resolve_taxon_filter("I want to see plants", client)

    assert taxon_filter == {"taxonRank": "kingdom", "taxonValue": "Plantae"}