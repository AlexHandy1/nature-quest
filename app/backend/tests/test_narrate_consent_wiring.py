from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from routers.narration import _generate_narrative


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="A walk through the park.")],
        usage=SimpleNamespace(input_tokens=400, output_tokens=150),
    )
    return client


def test_consent_false_builds_a_non_observed_client_with_no_extra_kwargs():
    client = _mock_client()
    with patch("routers.narration.ai_observability.build_client", return_value=client) as mock_build:
        _generate_narrative([], distinct_id="anon-1", consent=False)

    mock_build.assert_called_once_with(consent=False, distinct_id="anon-1", api_key=None)
    _, kwargs = client.messages.create.call_args
    assert "posthog_distinct_id" not in kwargs


def test_consent_true_builds_an_observed_client_and_passes_distinct_id():
    client = _mock_client()
    with patch("routers.narration.ai_observability.build_client", return_value=client) as mock_build:
        _generate_narrative([], distinct_id="anon-1", consent=True)

    mock_build.assert_called_once_with(consent=True, distinct_id="anon-1", api_key=None)
    _, kwargs = client.messages.create.call_args
    assert kwargs["posthog_distinct_id"] == "anon-1"


def test_returns_the_narrative_and_token_usage():
    client = _mock_client()
    with patch("routers.narration.ai_observability.build_client", return_value=client):
        narrative, usage = _generate_narrative([], distinct_id="anon-1", consent=False)

    assert narrative == "A walk through the park."
    assert usage == {"input_tokens": 400, "output_tokens": 150}
