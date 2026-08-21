import os

import pytest

from services.anthropic_client import build_client
from services.narration import generate_narrative
from services.tts import resolve_api_key, synthesize_speech
from services.wikipedia_client import fetch_species_summary

SAMPLE_WALK = [
    {"common_name": "Eurasian Magpie", "species": "Pica pica", "hotspot_lat": 40.414848, "hotspot_lon": -3.684565},
    {"common_name": "Iberian Green Woodpecker", "species": "Picus sharpei", "hotspot_lat": 40.413755, "hotspot_lon": -3.684227},
    {"common_name": "Egyptian Goose", "species": "Alopochen aegyptiaca", "hotspot_lat": 40.414395, "hotspot_lon": -3.682108},
    {"common_name": "Black Swan", "species": "Cygnus atratus", "hotspot_lat": 40.413864, "hotspot_lon": -3.681692},
    {"common_name": "Mallard", "species": "Anas platyrhynchos", "hotspot_lat": 40.415567, "hotspot_lon": -3.683259},
]

MP3_MAGIC_PREFIXES = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")

# Coarse plausibility bounds for ~45s of spoken narrative, not a bitrate
# assertion — wide enough to tolerate provider bitrate variance, narrow
# enough to catch an empty/truncated response or an error page slipping
# through as a 200.
MIN_PLAUSIBLE_AUDIO_BYTES = 10_000
MAX_PLAUSIBLE_AUDIO_BYTES = 5_000_000


def _resolve_openrouter_api_key() -> str | None:
    return resolve_api_key() or os.environ.get("OPENROUTER_API_KEY")


@pytest.fixture(scope="session")
def narrated_audio():
    client = build_client()
    species_list = [
        {**sp, **fetch_species_summary(sp["common_name"], sp["species"])}
        for sp in SAMPLE_WALK
    ]
    narrative = generate_narrative(species_list, client)
    audio_bytes = synthesize_speech(narrative, api_key=_resolve_openrouter_api_key())
    print(
        f"\n{'=' * 70}\n"
        f"NARRATIVE ({len(narrative)} chars):\n{narrative}\n"
        f"{'-' * 70}\n"
        f"AUDIO: {len(audio_bytes)} bytes, header={audio_bytes[:4]!r}\n"
        f"{'=' * 70}\n"
    )
    return narrative, audio_bytes


@pytest.mark.eval
def test_audio_is_returned_as_valid_mp3_bytes(narrated_audio):
    _, audio_bytes = narrated_audio
    assert audio_bytes.startswith(MP3_MAGIC_PREFIXES), f"unexpected header: {audio_bytes[:4]!r}"


@pytest.mark.eval
def test_audio_size_is_plausible_for_a_spoken_narrative(narrated_audio):
    _, audio_bytes = narrated_audio
    assert MIN_PLAUSIBLE_AUDIO_BYTES < len(audio_bytes) < MAX_PLAUSIBLE_AUDIO_BYTES, (
        f"{len(audio_bytes)} bytes outside plausible range "
        f"({MIN_PLAUSIBLE_AUDIO_BYTES}-{MAX_PLAUSIBLE_AUDIO_BYTES})"
    )
