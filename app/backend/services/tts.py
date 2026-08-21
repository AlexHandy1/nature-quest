import os

import google.auth
import httpx
from google.cloud import secretmanager

SECRET_ID = "openrouter-api-key"

MODEL = "hexgrad/kokoro-82m"
DEFAULT_VOICE = "af_alloy"
TTS_URL = "https://openrouter.ai/api/v1/audio/speech"
REQUEST_TIMEOUT = 60.0


def resolve_api_key() -> str | None:
    if os.environ.get("K_SERVICE"):
        return _fetch_api_key_from_secret_manager()
    return None


def _fetch_api_key_from_secret_manager() -> str:
    _, project_id = google.auth.default()
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{SECRET_ID}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


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
