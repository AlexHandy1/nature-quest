from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from routers.query import _resolve_taxon_filters


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(arguments='{"taxonFilters": []}')
                        )
                    ]
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=15),
    )
    return client


def test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs():
    client = _mock_client()
    with patch(
        "routers.query.ai_observability.build_openrouter_client", return_value=client
    ) as mock_build:
        _resolve_taxon_filters("birds", distinct_id="anon-1", consent=False)

    mock_build.assert_called_once_with(consent=False, distinct_id="anon-1", api_key=None)
    _, kwargs = client.chat.completions.create.call_args
    assert "posthog_distinct_id" not in kwargs


def test_consent_true_builds_an_observed_client_and_passes_distinct_id():
    client = _mock_client()
    with patch(
        "routers.query.ai_observability.build_openrouter_client", return_value=client
    ) as mock_build:
        _resolve_taxon_filters("birds", distinct_id="anon-1", consent=True)

    mock_build.assert_called_once_with(consent=True, distinct_id="anon-1", api_key=None)
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["posthog_distinct_id"] == "anon-1"


def test_returns_the_taxon_filter_and_token_usage():
    client = _mock_client()
    with patch("routers.query.ai_observability.build_openrouter_client", return_value=client):
        taxon_filters, usage = _resolve_taxon_filters("birds", distinct_id="anon-1", consent=False)

    assert taxon_filters == []
    assert usage == {"input_tokens": 120, "output_tokens": 15}
