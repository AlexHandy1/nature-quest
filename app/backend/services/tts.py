import os
import threading

import httpx

from services.logging_client import timed_checkpoint
from services.secret_manager_client import fetch_secret

SECRET_ID = "openrouter-api-key"

MODEL = "hexgrad/kokoro-82m"
DEFAULT_VOICE = "af_alloy"
TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
REQUEST_TIMEOUT = 60.0

_cached_api_key: str | None = None
_cached_api_key_lock = threading.Lock()


def resolve_api_key() -> str | None:
    """Fetches the OpenRouter key from Secret Manager at most once per
    process on Cloud Run, reusing it for the rest of the container's
    lifetime - see docs/status_docs/REPORT_duckdb_caching_latency_experiment_221026.md.
    Rotating this secret only takes effect on already-running instances
    after a redeploy (see infra/README.md)."""
    global _cached_api_key
    if not os.environ.get("K_SERVICE"):
        return None
    if _cached_api_key is None:
        with _cached_api_key_lock:
            if _cached_api_key is None:
                with timed_checkpoint("t_fetch_secret_openrouter"):
                    _cached_api_key = fetch_secret(SECRET_ID)
    return _cached_api_key


def reset() -> None:
    global _cached_api_key
    with _cached_api_key_lock:
        _cached_api_key = None


def synthesize_speech(text: str, api_key: str, voice: str = DEFAULT_VOICE) -> bytes:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "input": text,
        "voice": voice,
        # Without this, the endpoint defaults to raw headerless PCM, which no
        # browser can play from a plain <audio src> — confirmed in
        # narration_tts_spike.py, not documented on the model page.
        "response_format": "mp3",
    }
    try:
        response = httpx.post(TTS_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter (Kokoro-82M) TTS request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter (Kokoro-82M) TTS failed ({response.status_code}): {response.text[:500]}"
        )
    return response.content
