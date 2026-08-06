import pytest
from deepeval.tracing import observe, update_current_span

from services.anthropic_client import build_client, resolve_taxon_filter


@observe(type="llm", name="resolve_taxon_filter")
def _resolve(query: str) -> dict | None:
    taxon_filter = resolve_taxon_filter(query, build_client())
    update_current_span(input=query, output=taxon_filter)
    print(f"[eval] resolve_taxon_filter input={query!r} output={taxon_filter!r}")
    return taxon_filter


@pytest.mark.eval
def test_resolves_a_birds_query_to_the_aves_class_filter():
    taxon_filter = _resolve("I want to see birds")

    assert taxon_filter == {"taxonRank": "class", "taxonValue": "Aves"}
