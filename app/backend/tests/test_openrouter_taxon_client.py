import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.anthropic_client import QUERY_SCHEMA_TOOL, TAXON_GUIDANCE
from services.openrouter_taxon_client import (
    MODEL,
    QUERY_SCHEMA_TOOL_OPENAI,
    REQUEST_TIMEOUT,
    resolve_taxon_filters,
)


def _mock_client(arguments: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="produce_gbif_query", arguments=arguments
                            )
                        )
                    ]
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=15),
    )
    return client


def test_returns_the_resolved_taxon_filter():
    client = _mock_client(json.dumps({"taxonFilters": [{"taxonRank": "class", "taxonValue": "Aves"}]}))

    taxon_filters = resolve_taxon_filters("I want to see birds", client)

    assert taxon_filters == [{"taxonRank": "class", "taxonValue": "Aves"}]


def test_returns_multiple_taxon_filters_for_a_mixed_taxa_query():
    client = _mock_client(
        json.dumps(
            {
                "taxonFilters": [
                    {"taxonRank": "class", "taxonValue": "Aves"},
                    {"taxonRank": "kingdom", "taxonValue": "Plantae"},
                ]
            }
        )
    )

    taxon_filters = resolve_taxon_filters("birds and plants", client)

    assert taxon_filters == [
        {"taxonRank": "class", "taxonValue": "Aves"},
        {"taxonRank": "kingdom", "taxonValue": "Plantae"},
    ]


def test_forces_structured_output_via_the_taxon_tool():
    client = _mock_client(json.dumps({"taxonFilters": []}))

    resolve_taxon_filters("I want to see birds", client)

    client.chat.completions.create.assert_called_once_with(
        model=MODEL,
        messages=[
            {"role": "system", "content": TAXON_GUIDANCE},
            {"role": "user", "content": "I want to see birds"},
        ],
        tools=[QUERY_SCHEMA_TOOL_OPENAI],
        tool_choice={"type": "function", "function": {"name": "produce_gbif_query"}},
        timeout=REQUEST_TIMEOUT,
    )


def test_query_schema_tool_openai_wraps_the_same_schema_anthropic_uses():
    assert QUERY_SCHEMA_TOOL_OPENAI == {
        "type": "function",
        "function": {
            "name": QUERY_SCHEMA_TOOL["name"],
            "description": QUERY_SCHEMA_TOOL["description"],
            "parameters": QUERY_SCHEMA_TOOL["input_schema"],
        },
    }


def test_invokes_on_response_with_the_raw_response():
    client = _mock_client(json.dumps({"taxonFilters": []}))
    seen = []

    resolve_taxon_filters("I want to see birds", client, on_response=seen.append)

    assert seen == [client.chat.completions.create.return_value]


def test_passes_extra_kwargs_through_to_the_create_call():
    client = _mock_client(json.dumps({"taxonFilters": []}))

    resolve_taxon_filters("I want to see birds", client, posthog_distinct_id="anon-1")

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["posthog_distinct_id"] == "anon-1"


def test_raises_when_the_model_does_not_call_the_tool():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )

    with pytest.raises(Exception):
        resolve_taxon_filters("I want to see birds", client)


def test_raises_when_the_tool_call_arguments_are_not_valid_json():
    client = _mock_client("not valid json")

    with pytest.raises(Exception):
        resolve_taxon_filters("I want to see birds", client)


def test_propagates_a_timeout_error_from_the_client():
    client = MagicMock()
    client.chat.completions.create.side_effect = TimeoutError("request timed out")

    with pytest.raises(TimeoutError):
        resolve_taxon_filters("I want to see birds", client)


def test_no_model_specific_logic_outside_the_model_constant():
    import inspect

    import services.openrouter_taxon_client as module

    source = inspect.getsource(module)
    occurrences = source.count(MODEL)
    assert occurrences == 1, "MODEL string must appear exactly once (its own definition)"
