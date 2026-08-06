from unittest.mock import MagicMock, patch

from anthropic import Anthropic
from posthog.ai.anthropic import Anthropic as ObservedAnthropic

from services.ai_observability import build_client


def test_consent_false_returns_a_plain_anthropic_client():
    client = build_client(consent=False, distinct_id="anon-1")

    assert type(client) is Anthropic


def test_consent_true_returns_a_posthog_observed_client():
    client = build_client(consent=True, distinct_id="anon-1", posthog_client=MagicMock())

    assert isinstance(client, ObservedAnthropic)


def test_consent_true_without_injected_client_builds_one_from_env_vars():
    with patch(
        "services.ai_observability.PostHogClient"
    ) as mock_posthog_client_class, patch.dict(
        "os.environ",
        {"POSTHOG_PROJECT_TOKEN": "phc_test_token", "POSTHOG_HOST": "https://eu.i.posthog.com"},
    ):
        build_client(consent=True, distinct_id="anon-1")

    mock_posthog_client_class.assert_called_once_with(
        "phc_test_token", host="https://eu.i.posthog.com"
    )


def test_default_posthog_client_is_reused_across_calls():
    with patch(
        "services.ai_observability.PostHogClient"
    ) as mock_posthog_client_class, patch.dict(
        "os.environ",
        {"POSTHOG_PROJECT_TOKEN": "phc_test_token"},
    ):
        build_client(consent=True, distinct_id="anon-1")
        build_client(consent=True, distinct_id="anon-2")

    mock_posthog_client_class.assert_called_once()
