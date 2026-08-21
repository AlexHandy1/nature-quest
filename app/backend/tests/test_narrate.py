import base64
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from services.narration_budget import DAILY_NARRATION_CALL_CAP, try_consume_daily_budget

client = TestClient(app)


def _species(common_name="Eurasian Magpie", species="Pica pica"):
    return {
        "common_name": common_name,
        "species": species,
        "hotspot_lat": 40.4148,
        "hotspot_lon": -3.6846,
        "extract": "A common corvid.",
    }


def _narrate_body(species_count=5):
    return {
        "species": [_species(common_name=f"Species {i}", species=f"Sp{i} sp") for i in range(species_count)],
        "distinctId": "anon-1",
    }


def test_fewer_than_five_species_returns_422():
    response = client.post("/api/narrate", json=_narrate_body(species_count=3))

    assert response.status_code == 422


def test_more_than_five_species_returns_422():
    response = client.post("/api/narrate", json=_narrate_body(species_count=6))

    assert response.status_code == 422


def test_common_name_over_max_length_returns_422_with_no_llm_or_tts_call():
    body = _narrate_body()
    body["species"][0]["common_name"] = "a" * 501

    with (
        patch("routers.narration.generate_narrative") as mock_generate,
        patch("routers.narration.synthesize_speech") as mock_tts,
    ):
        response = client.post("/api/narrate", json=body)

    assert response.status_code == 422
    mock_generate.assert_not_called()
    mock_tts.assert_not_called()


def test_scientific_name_over_max_length_returns_422_with_no_llm_or_tts_call():
    body = _narrate_body()
    body["species"][0]["species"] = "a" * 501

    with (
        patch("routers.narration.generate_narrative") as mock_generate,
        patch("routers.narration.synthesize_speech") as mock_tts,
    ):
        response = client.post("/api/narrate", json=body)

    assert response.status_code == 422
    mock_generate.assert_not_called()
    mock_tts.assert_not_called()


def test_extract_over_max_length_returns_422_with_no_llm_or_tts_call():
    body = _narrate_body()
    body["species"][0]["extract"] = "a" * 2001

    with (
        patch("routers.narration.generate_narrative") as mock_generate,
        patch("routers.narration.synthesize_speech") as mock_tts,
    ):
        response = client.post("/api/narrate", json=body)

    assert response.status_code == 422
    mock_generate.assert_not_called()
    mock_tts.assert_not_called()


def test_resolved_narration_returns_narrative_and_base64_audio():
    with (
        patch("routers.narration.generate_narrative", return_value="A walk through the park."),
        patch("routers.narration.synthesize_speech", return_value=b"fake-mp3-bytes"),
        patch("routers.narration.log_narration_outcome") as mock_log,
    ):
        response = client.post("/api/narrate", json=_narrate_body())

    body = response.json()
    assert response.status_code == 200
    assert body["narrative"] == "A walk through the park."
    assert base64.b64decode(body["audio"]) == b"fake-mp3-bytes"
    mock_log.assert_called_once_with("resolved", distinct_id="anon-1")


def test_narration_declined_by_model_returns_422_with_no_tts_call():
    with (
        patch("routers.narration.generate_narrative", return_value=None),
        patch("routers.narration.synthesize_speech") as mock_tts,
        patch("routers.narration.log_narration_outcome") as mock_log,
    ):
        response = client.post("/api/narrate", json=_narrate_body())

    assert response.status_code == 422
    assert response.json()["status"] == "narration_declined"
    mock_tts.assert_not_called()
    mock_log.assert_called_once_with("declined", distinct_id="anon-1")


def test_tts_failure_returns_502():
    with (
        patch("routers.narration.generate_narrative", return_value="A walk through the park."),
        patch("routers.narration.synthesize_speech", side_effect=RuntimeError("boom")),
        patch("routers.narration.log_narration_outcome") as mock_log,
    ):
        response = client.post("/api/narrate", json=_narrate_body())

    assert response.status_code == 502
    assert response.json()["status"] == "tts_unavailable"
    mock_log.assert_called_once_with("tts_unavailable", distinct_id="anon-1")


def test_daily_budget_exhausted_returns_429_with_no_llm_call():
    for _ in range(DAILY_NARRATION_CALL_CAP):
        try_consume_daily_budget(datetime.now(tz=timezone.utc).date())

    with (
        patch("routers.narration.generate_narrative") as mock_generate,
        patch("routers.narration.log_narration_outcome") as mock_log,
    ):
        response = client.post("/api/narrate", json=_narrate_body())

    assert response.status_code == 429
    assert response.json()["error"] == "daily_limit_reached"
    mock_generate.assert_not_called()
    mock_log.assert_called_once_with("daily_limit_reached", distinct_id="anon-1", guardrail="daily_limit")
