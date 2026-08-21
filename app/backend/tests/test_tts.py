from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from services.tts import resolve_api_key, synthesize_speech


def test_resolve_api_key_returns_none_locally():
    with patch.dict("os.environ", {}, clear=True):
        assert resolve_api_key() is None


def test_resolve_api_key_fetches_from_secret_manager_on_cloud_run():
    mock_client = MagicMock()
    mock_client.access_secret_version.return_value = SimpleNamespace(
        payload=SimpleNamespace(data=b"sk-or-test-key")
    )
    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch(
            "services.tts.google.auth.default",
            return_value=(MagicMock(), "my-project"),
        ),
        patch(
            "services.tts.secretmanager.SecretManagerServiceClient",
            return_value=mock_client,
        ),
    ):
        key = resolve_api_key()

    assert key == "sk-or-test-key"
    mock_client.access_secret_version.assert_called_once_with(
        request={"name": "projects/my-project/secrets/openrouter-api-key/versions/latest"}
    )


def test_synthesize_speech_returns_audio_bytes_on_success():
    with patch("services.tts.httpx.post") as mock_post:
        mock_post.return_value = SimpleNamespace(status_code=200, content=b"fake-mp3-bytes")

        audio_bytes = synthesize_speech("A walk through the park.", api_key="test-key")

    assert audio_bytes == b"fake-mp3-bytes"


def test_synthesize_speech_raises_on_a_non_200_response():
    with patch("services.tts.httpx.post") as mock_post:
        mock_post.return_value = SimpleNamespace(status_code=402, text="paid_plan_required")

        try:
            synthesize_speech("A walk through the park.", api_key="test-key")
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "402" in str(exc)


def test_synthesize_speech_raises_on_a_request_failure():
    with patch("services.tts.httpx.post", side_effect=httpx.HTTPError("boom")):
        try:
            synthesize_speech("A walk through the park.", api_key="test-key")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass
