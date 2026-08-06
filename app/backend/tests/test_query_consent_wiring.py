from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from routers.query import _resolve_taxon_filter


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input={"taxonFilter": None})]
    )
    return client


def test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs():
    client = _mock_client()
    with patch("routers.query.ai_observability.build_client", return_value=client) as mock_build:
        _resolve_taxon_filter("birds", distinct_id="anon-1", consent=False)

    mock_build.assert_called_once_with(consent=False, distinct_id="anon-1", api_key=None)
    _, kwargs = client.messages.create.call_args
    assert "posthog_distinct_id" not in kwargs


def test_consent_true_builds_an_observed_client_and_passes_distinct_id():
    client = _mock_client()
    with patch("routers.query.ai_observability.build_client", return_value=client) as mock_build:
        _resolve_taxon_filter("birds", distinct_id="anon-1", consent=True)

    mock_build.assert_called_once_with(consent=True, distinct_id="anon-1", api_key=None)
    _, kwargs = client.messages.create.call_args
    assert kwargs["posthog_distinct_id"] == "anon-1"
