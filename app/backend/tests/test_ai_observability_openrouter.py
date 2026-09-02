from unittest.mock import MagicMock, patch

from openai import OpenAI
from posthog.ai.openai import OpenAI as ObservedOpenAI

from services.ai_observability import OPENROUTER_BASE_URL, build_openrouter_client


def test_consent_false_returns_a_plain_openai_client():
    client = build_openrouter_client(consent=False, distinct_id="anon-1", api_key="test-key")

    assert type(client) is OpenAI
    assert str(client.base_url).rstrip("/") == OPENROUTER_BASE_URL.rstrip("/")


def test_consent_true_returns_a_posthog_observed_client():
    client = build_openrouter_client(
        consent=True, distinct_id="anon-1", api_key="test-key", posthog_client=MagicMock()
    )

    assert isinstance(client, ObservedOpenAI)
    assert str(client.base_url).rstrip("/") == OPENROUTER_BASE_URL.rstrip("/")


def test_consent_true_without_injected_client_builds_one_from_env_vars():
    with patch(
        "services.ai_observability.PostHogClient"
    ) as mock_posthog_client_class, patch.dict(
        "os.environ",
        {"POSTHOG_PROJECT_TOKEN": "phc_test_token", "POSTHOG_HOST": "https://eu.i.posthog.com"},
    ):
        build_openrouter_client(consent=True, distinct_id="anon-1", api_key="test-key")

    mock_posthog_client_class.assert_called_once_with(
        "phc_test_token", host="https://eu.i.posthog.com"
    )


def test_default_posthog_client_is_shared_with_the_anthropic_branch():
    from services.ai_observability import build_client

    with patch(
        "services.ai_observability.PostHogClient"
    ) as mock_posthog_client_class, patch.dict(
        "os.environ",
        {"POSTHOG_PROJECT_TOKEN": "phc_test_token"},
    ):
        build_client(consent=True, distinct_id="anon-1")
        build_openrouter_client(consent=True, distinct_id="anon-2", api_key="test-key")

    mock_posthog_client_class.assert_called_once()
