import os
import threading

from anthropic import Anthropic
from openai import OpenAI
from posthog import Client as PostHogClient
from posthog.ai.anthropic import Anthropic as ObservedAnthropic
from posthog.ai.openai import OpenAI as ObservedOpenAI

from services.logging_client import timed_checkpoint

DEFAULT_POSTHOG_HOST = "https://eu.i.posthog.com"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_posthog_client: PostHogClient | None = None

# distinct_id is passed per-call to chat.completions.create() (see
# routers/query.py), not baked into client construction, so one cached
# client per consent state is safely reusable across every user's request.
_openrouter_clients: dict[bool, OpenAI] = {}
_openrouter_clients_lock = threading.Lock()


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


def build_openrouter_client(
    consent: bool,
    distinct_id: str,
    api_key: str | None = None,
    posthog_client=None,
) -> OpenAI:
    """Builds the OpenRouter client at most once per consent state per
    process, reusing it for the rest of the container's lifetime - see
    docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md."""
    with timed_checkpoint("t_build_openrouter_client", consent=consent):
        if consent not in _openrouter_clients:
            with _openrouter_clients_lock:
                if consent not in _openrouter_clients:
                    _openrouter_clients[consent] = _build_openrouter_client(
                        consent, api_key, posthog_client
                    )
        return _openrouter_clients[consent]


def _build_openrouter_client(consent: bool, api_key: str | None, posthog_client) -> OpenAI:
    if not consent:
        return OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    posthog_client = posthog_client or _default_posthog_client()
    return ObservedOpenAI(
        api_key=api_key, base_url=OPENROUTER_BASE_URL, posthog_client=posthog_client
    )


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
    with _openrouter_clients_lock:
        _openrouter_clients.clear()
