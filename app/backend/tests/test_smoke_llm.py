import pytest

from services.ai_observability import build_client
from services.anthropic_client import resolve_api_key, resolve_taxon_filters


def _build_client():
    return build_client(consent=False, distinct_id="smoke-test", api_key=resolve_api_key())


@pytest.mark.eval
def test_resolves_a_birds_query_to_the_aves_class_filter():
    client = _build_client()

    taxon_filters = resolve_taxon_filters("I want to see birds", client)

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]

@pytest.mark.eval
def test_resolves_a_plants_query_to_the_plantae_kingdom_filter():
    client = _build_client()

    taxon_filters = resolve_taxon_filters("I want to see plants", client)

    assert taxon_filters == [{"taxonRank": "kingdom", "taxonValue": "Plantae"}]