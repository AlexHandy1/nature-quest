#!/usr/bin/env python3
"""
PROTOTYPE — narration_tts_spike.py
Question: given 5 species names + locations, can a plain Anthropic call write a
short nature-documentary-style narrative, and can ElevenLabs turn that straight
into audio via a simple API call? How long does the whole round trip take?
Throwaway. Do not promote to production.

Deliberately minimal — a first pass at comparing TTS providers before exploring
streaming. Narrative input is names + locations only, no Wikipedia extract (see
prototypes/README.md open item list). `--test-model` selects the TTS provider:
`elevenlabs` (default) or `openrouter` (Kokoro-82M via OpenRouter's
OpenAI-compatible /v1/audio/speech endpoint).

Species list is a fixed sample (Retiro Park, mixed birds/plants) hardcoded
below, captured from a live e2e_walk_spike_full_validation.py run, so repeat
runs are comparable.

Uses the plain Anthropic Messages API (not the Agent SDK) per this repo's
already-validated pattern (species_narrative_cost_experiment2.py) — Haiku by
default. Both TTS providers are called directly via `requests`, same style as
gbif_client.py / wikipedia_client.py, rather than adding an SDK as a new dep.

Requires ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, and/or OPENROUTER_API_KEY
(depending on --test-model) in the environment.

Run: source venv/bin/activate && python prototypes/scripts/narration_tts_spike.py \
    [--test-model elevenlabs|openrouter] \
    2>&1 | tee prototypes/logs/narration_tts_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import os
import sys
import time
import webbrowser

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

NARRATIVE_MODEL = "claude-haiku-4-5-20251001"
NARRATIVE_MAX_TOKENS = 300

# ElevenLabs premade voice "Daniel" ("Steady Broadcaster") — confirmed present
# in this account's /v1/voices list (category=premade). Library/professional
# voices (e.g. G17SuINrv2H9FC6nvetn, "Christopher") 402'd with
# paid_plan_required even after fixing API key permissions.
# Overridable via ELEVENLABS_VOICE_ID.
DEFAULT_ELEVENLABS_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Kokoro-82M via OpenRouter — OpenAI-compatible /v1/audio/speech endpoint, per
# the request shape shown on openrouter.ai/hexgrad/kokoro-82m's playground.
# Overridable via OPENROUTER_VOICE.
DEFAULT_OPENROUTER_VOICE = "af_alloy"
OPENROUTER_MODEL = "hexgrad/kokoro-82m"
OPENROUTER_TTS_URL = "https://openrouter.ai/api/v1/audio/speech"

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

# Fixed sample walk — same 5 Retiro species as species_narrative_spike.py's
# HARDCODED_SPECIES, trimmed to just what this prototype's prompt needs
# (name + location). Kept as a separate constant, not imported, per this
# folder's "standalone scripts" convention.
SAMPLE_WALK = {
    "species": [
        {"common_name": "Eurasian Magpie", "scientific_name": "Pica pica",
         "lat": 40.414848, "lon": -3.684565},
        {"common_name": "Iberian Green Woodpecker", "scientific_name": "Picus sharpei",
         "lat": 40.413755, "lon": -3.684227},
        {"common_name": "Egyptian Goose", "scientific_name": "Alopochen aegyptiaca",
         "lat": 40.414395, "lon": -3.682108},
        {"common_name": "Black Swan", "scientific_name": "Cygnus atratus",
         "lat": 40.413864, "lon": -3.681692},
        {"common_name": "Mallard", "scientific_name": "Anas platyrhynchos",
         "lat": 40.415567, "lon": -3.683259},
    ],
}


def header(title):
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


def build_narrative_prompt(walk):
    lines = []
    for i, sp in enumerate(walk["species"], 1):
        lines.append(
            f"{i}. {sp['common_name']} ({sp['scientific_name']}) at "
            f"({sp['lat']:.4f}, {sp['lon']:.4f})"
        )
    species_block = "\n".join(lines)

    return f"""You are narrating a nature walk, in the style of a wildlife
documentary narrator — full of wonder, adventure, and a sense of discovery.

The walk visits these {len(walk['species'])} species, in order, each at its own
GPS coordinate:

{species_block}

Infer where in the world this walk is taking place from the coordinates and the
species themselves. Write a single flowing narrative guide of roughly 120-160
words (about 45-60 seconds spoken aloud) for a walker following this route. You
have only each species' name and location to work from — draw on your own
knowledge of these species, and weave in a sense of place and journey. Write
continuous narrated prose, not a list. Do not use markdown, and do not include
a title or heading — start straight into the narration."""


def generate_narrative(walk, client):
    prompt = build_narrative_prompt(walk)

    print(f"\n  {DIM}--- PROMPT ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.monotonic()
    response = client.messages.create(
        model=NARRATIVE_MODEL,
        max_tokens=NARRATIVE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.monotonic() - start

    narrative = "".join(block.text for block in response.content if block.type == "text").strip()

    print(f"\n  {DIM}--- RESPONSE ({elapsed_s:.1f}s) ---{RESET}")
    print(f"  {narrative}")
    print(
        f"  {DIM}[in={response.usage.input_tokens} out={response.usage.output_tokens} "
        f"model={NARRATIVE_MODEL}]{RESET}"
    )

    return narrative, elapsed_s


def synthesize_speech_elevenlabs(text, api_key, voice_id):
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
    }

    start = time.monotonic()
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    elapsed_s = time.monotonic() - start

    if response.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs TTS failed ({response.status_code}): {response.text[:500]}"
        )

    return response.content, elapsed_s


def synthesize_speech_openrouter(text, api_key, voice):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "input": text,
        "voice": voice,
        # Without this, the endpoint defaults to raw headerless PCM
        # (audio/pcm;rate=24000;channels=1), which no browser can play from a
        # plain <audio src> — confirmed live, not documented on the model page.
        "response_format": "mp3",
    }

    start = time.monotonic()
    response = requests.post(OPENROUTER_TTS_URL, headers=headers, json=payload, timeout=60)
    elapsed_s = time.monotonic() - start

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter (Kokoro-82M) TTS failed ({response.status_code}): {response.text[:500]}"
        )

    return response.content, elapsed_s


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test-model",
        choices=["elevenlabs", "openrouter"],
        default="elevenlabs",
        help="TTS provider to test: elevenlabs (default) or openrouter (Kokoro-82M).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    provider = args.test_model
    provider_label = "ElevenLabs" if provider == "elevenlabs" else "OpenRouter (Kokoro-82M)"

    print(f"\n{BOLD}PROTOTYPE: Narrative -> {provider_label} TTS spike{RESET}")
    print(f"{DIM}Question: names+locations -> narrative (plain Anthropic call) -> {provider_label} audio{RESET}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print(f"\n{RED}ANTHROPIC_API_KEY is not set.{RESET}")
        sys.exit(1)

    if provider == "elevenlabs":
        tts_key = os.environ.get("ELEVENLABS_API_KEY")
        if not tts_key:
            print(f"\n{RED}ELEVENLABS_API_KEY is not set.{RESET}")
            sys.exit(1)
        voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)
    else:
        tts_key = os.environ.get("OPENROUTER_API_KEY")
        if not tts_key:
            print(f"\n{RED}OPENROUTER_API_KEY is not set.{RESET}")
            sys.exit(1)
        voice = os.environ.get("OPENROUTER_VOICE", DEFAULT_OPENROUTER_VOICE)

    client = Anthropic(api_key=anthropic_key)

    header("STEP 1: Generate narrative")
    narrative, narrative_elapsed_s = generate_narrative(SAMPLE_WALK, client)

    header(f"STEP 2: {provider_label} text-to-speech")
    if provider == "elevenlabs":
        print(f"  {DIM}voice_id={voice} model_id={ELEVENLABS_MODEL_ID}{RESET}")
        audio_bytes, tts_elapsed_s = synthesize_speech_elevenlabs(narrative, tts_key, voice)
    else:
        print(f"  {DIM}voice={voice} model={OPENROUTER_MODEL}{RESET}")
        audio_bytes, tts_elapsed_s = synthesize_speech_openrouter(narrative, tts_key, voice)
    print(f"  {GREEN}Received {len(audio_bytes):,} bytes in {tts_elapsed_s:.1f}s{RESET}")

    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    audio_filename = f"narration_tts_spike_{provider}.mp3"
    audio_path = os.path.join(artifacts_dir, audio_filename)
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)
    print(f"  Written: {audio_path}")

    header("SUMMARY — timing")
    total_s = narrative_elapsed_s + tts_elapsed_s
    print(f"  {'Step':<20} {'Time':>8}")
    print(f"  {'narrative (Haiku)':<20} {narrative_elapsed_s:>7.1f}s")
    print(f"  {provider_label + ' TTS':<20} {tts_elapsed_s:>7.1f}s")
    print(f"  {'-' * 30}")
    print(f"  {'TOTAL':<20} {total_s:>7.1f}s")
    print(f"  narrative length: {len(narrative)} chars")

    html_path = generate_player_page(
        artifacts_dir, provider, audio_filename, provider_label, narrative, narrative_elapsed_s, tts_elapsed_s
    )
    print(f"\n{GREEN}Opening player -> {html_path}{RESET}\n")
    webbrowser.open(f"file://{html_path}")


def generate_player_page(artifacts_dir, provider, audio_filename, provider_label, narrative, narrative_elapsed_s, tts_elapsed_s):
    total_s = narrative_elapsed_s + tts_elapsed_s
    narrative_html = narrative.replace("\n\n", "</p><p>").replace("\n", " ")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — narration TTS spike</title>
<style>
  body {{ font-family: sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; color: #222; }}
  #banner {{ display: inline-block; background: #ffd700; padding: 3px 12px; border-radius: 4px;
    font-size: 11px; font-weight: bold; margin-bottom: 16px; }}
  audio {{ width: 100%; margin: 16px 0; }}
  .timing {{ font-size: 12px; color: #666; margin-bottom: 20px; }}
  p {{ line-height: 1.6; }}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — narrative + {provider_label} TTS</div>
<audio controls src="{audio_filename}"></audio>
<div class="timing">
  narrative generation: {narrative_elapsed_s:.1f}s &nbsp;|&nbsp;
  TTS generation: {tts_elapsed_s:.1f}s &nbsp;|&nbsp;
  total: {total_s:.1f}s
</div>
<p>{narrative_html}</p>
</body>
</html>"""

    out_filename = f"narration_tts_spike_{provider}.html"
    out_path = os.path.join(artifacts_dir, out_filename)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    main()
