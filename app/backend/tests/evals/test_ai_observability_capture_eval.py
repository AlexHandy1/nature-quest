import os

import pytest

from services import ai_observability
from services.anthropic_client import resolve_api_key, resolve_taxon_filter

REQUIRES_POSTHOG = pytest.mark.skipif(
    "POSTHOG_PROJECT_TOKEN" not in os.environ,
    reason="POSTHOG_PROJECT_TOKEN not set locally — skipping real PostHog capture check",
)


@pytest.mark.eval
@REQUIRES_POSTHOG
def test_consent_true_sends_a_real_ai_generation_event_to_posthog():
    client = ai_observability.build_client(
        consent=True, distinct_id="eval-suite", api_key=resolve_api_key()
    )

    taxon_filter = resolve_taxon_filter(
        "I want to see birds", client, posthog_distinct_id="eval-suite"
    )
    ai_observability._posthog_client.flush()

    assert taxon_filter == {"taxonRank": "class", "taxonValue": "Aves"}
