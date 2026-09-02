import os

import pytest

from routers.query import _resolve_openrouter_api_key
from services import ai_observability
from services.anthropic_client import resolve_api_key, resolve_taxon_filters
from services.openrouter_taxon_client import resolve_taxon_filters as resolve_taxon_filters_openrouter

REQUIRES_POSTHOG = pytest.mark.skipif(
    "POSTHOG_PROJECT_TOKEN" not in os.environ,
    reason="POSTHOG_PROJECT_TOKEN not set locally — skipping real PostHog capture check",
)


@pytest.mark.eval
@REQUIRES_POSTHOG
def test_consent_true_sends_a_real_ai_generation_event_to_posthog():
    """Rollback-path coverage (PAT-001): anthropic_client.resolve_taxon_filters
    is no longer production's taxon-resolution path, but stays observability-
    covered so the manual rollback (swap the constant/import back) is verified."""
    client = ai_observability.build_client(
        consent=True, distinct_id="eval-suite", api_key=resolve_api_key()
    )

    taxon_filters = resolve_taxon_filters(
        "I want to see birds", client, posthog_distinct_id="eval-suite"
    )
    ai_observability._posthog_client.flush()

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]


@pytest.mark.eval
@REQUIRES_POSTHOG
def test_consent_true_sends_a_real_ai_generation_event_to_posthog_via_openrouter():
    client = ai_observability.build_openrouter_client(
        consent=True, distinct_id="eval-suite", api_key=_resolve_openrouter_api_key()
    )

    taxon_filters = resolve_taxon_filters_openrouter(
        "I want to see birds", client, posthog_distinct_id="eval-suite"
    )
    ai_observability._posthog_client.flush()

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]
