from unittest.mock import patch

from fastapi.testclient import TestClient

from main import create_app, warm_caches


def test_warm_caches_does_nothing_locally():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("main.species_index.get_shared_connection") as mock_connection,
        patch("main.tts.resolve_api_key") as mock_resolve_key,
        patch("main.ai_observability.build_openrouter_client") as mock_build_client,
    ):
        warm_caches()

    mock_connection.assert_not_called()
    mock_resolve_key.assert_not_called()
    mock_build_client.assert_not_called()


def test_warm_caches_builds_the_shared_db_connection_on_cloud_run():
    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch("main.species_index.get_shared_connection") as mock_connection,
        patch("main.tts.resolve_api_key", return_value="sk-or-test-key"),
        patch("main.ai_observability.build_openrouter_client"),
    ):
        warm_caches()

    mock_connection.assert_called_once()


def test_warm_caches_resolves_the_openrouter_key_on_cloud_run():
    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch("main.species_index.get_shared_connection"),
        patch("main.tts.resolve_api_key", return_value="sk-or-test-key") as mock_resolve_key,
        patch("main.ai_observability.build_openrouter_client"),
    ):
        warm_caches()

    mock_resolve_key.assert_called_once()


def test_warm_caches_builds_both_consent_state_clients_on_cloud_run():
    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch("main.species_index.get_shared_connection"),
        patch("main.tts.resolve_api_key", return_value="sk-or-test-key"),
        patch("main.ai_observability.build_openrouter_client") as mock_build_client,
    ):
        warm_caches()

    consents_built = {call.kwargs["consent"] for call in mock_build_client.call_args_list}
    assert consents_built == {False, True}
    for call in mock_build_client.call_args_list:
        assert call.kwargs["api_key"] == "sk-or-test-key"


def test_lifespan_runs_warm_caches_on_startup(tmp_path):
    app = create_app(static_dir=tmp_path)

    with (
        patch.dict("os.environ", {"K_SERVICE": "nature-quest-backend"}),
        patch("main.species_index.get_shared_connection") as mock_connection,
        patch("main.tts.resolve_api_key", return_value="sk-or-test-key"),
        patch("main.ai_observability.build_openrouter_client"),
        TestClient(app),
    ):
        mock_connection.assert_called_once()
