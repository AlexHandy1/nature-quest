from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.anthropic_client import (
    MODEL,
    QUERY_SCHEMA_TOOL,
    TAXON_GUIDANCE,
    resolve_api_key,
    resolve_taxon_filters,
)


def _mock_client(tool_input: dict) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=tool_input)]
    )
    return client


def test_returns_the_resolved_taxon_filter():
    client = _mock_client({"taxonFilters": [{"taxonRank": "class", "taxonValue": "Aves"}]})

    taxon_filters = resolve_taxon_filters("I want to see birds", client)

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]


def test_returns_multiple_taxon_filters_for_a_mixed_taxa_query():
    client = _mock_client(
        {
            "taxonFilters": [
                {"taxonRank": "class", "taxonValue": "Aves"},
                {"taxonRank": "kingdom", "taxonValue": "Plantae"},
            ]
        }
    )

    taxon_filters = resolve_taxon_filters("birds and plants", client)

    assert taxon_filters == [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
    ]


def test_forces_structured_output_via_the_taxon_tool():
    client = _mock_client({"taxonFilters": []})

    resolve_taxon_filters("I want to see birds", client)

    client.messages.create.assert_called_once_with(
        model=MODEL,
        max_tokens=1024,
        system=TAXON_GUIDANCE,
        tools=[QUERY_SCHEMA_TOOL],
        tool_choice={"type": "tool", "name": "produce_gbif_query"},
        messages=[{"role": "user", "content": "I want to see birds"}],
    )


def test_resolve_api_key_returns_none_locally():
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_api_key() is None


def test_resolve_api_key_fetches_from_secret_manager_on_cloud_run():
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = SimpleNamespace(
        payload=SimpleNamespace(data=b"sk-ant-test-key")
    )
    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch(
            "services.anthropic_client.google.auth.default",
            return_value=(MagicMock(), "my-project"),
        ),
        patch(
            "services.anthropic_client.secretmanager.SecretManagerServiceClient",
            return_value=mock_client,
        ),
    ):
        key = resolve_api_key()

    assert key == "sk-ant-test-key"
    mock_client.access_secret_version.assert_called_once_with(
        request={"name": "projects/my-project/secrets/anthropic-api-key/versions/latest"}
    )


def test_invokes_on_response_with_the_raw_response():
    client = _mock_client({"taxonFilters": []})
    seen = []

    resolve_taxon_filters("I want to see birds", client, on_response=seen.append)

    assert seen == [client.messages.create.return_value]


def test_passes_extra_kwargs_through_to_the_create_call():
    client = _mock_client({"taxonFilters": []})

    resolve_taxon_filters("I want to see birds", client, posthog_distinct_id="anon-1")

    client.messages.create.assert_called_once_with(
        model=MODEL,
        max_tokens=1024,
        system=TAXON_GUIDANCE,
        tools=[QUERY_SCHEMA_TOOL],
        tool_choice={"type": "tool", "name": "produce_gbif_query"},
        messages=[{"role": "user", "content": "I want to see birds"}],
        posthog_distinct_id="anon-1",
    )
