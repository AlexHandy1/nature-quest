import os

from anthropic import Anthropic
from posthog import Client as PostHogClient
from posthog.ai.anthropic import Anthropic as ObservedAnthropic

DEFAULT_POSTHOG_HOST = "https://eu.i.posthog.com"

_posthog_client: PostHogClient | None = None


def build_client(
    consent: bool,
    distinct_id: str,
    api_key: str | None = None,
    posthog_client=None,
) -> Anthropic:
    if not consent:
        return Anthropic(api_key=api_key)
    posthog_client = posthog_client or _default_posthog_client()
    return ObservedAnthropic(api_key=api_key, posthog_client=posthog_client)


def _default_posthog_client() -> PostHogClient:
    global _posthog_client
    if _posthog_client is None:
        _posthog_client = PostHogClient(
            os.environ["POSTHOG_PROJECT_TOKEN"],
            host=os.environ.get("POSTHOG_HOST", DEFAULT_POSTHOG_HOST),
        )
    return _posthog_client


def reset() -> None:
    global _posthog_client
    _posthog_client = None
