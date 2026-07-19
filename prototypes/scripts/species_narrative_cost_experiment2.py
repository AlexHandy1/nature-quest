#!/usr/bin/env python3
"""
PROTOTYPE — species_narrative_cost_experiment2.py
Cost-optimisation experiment forked from species_narrative_cost_experiment1.py.

Question: does dropping the Claude Agent SDK entirely — no agent loop, no
`claude` CLI subprocess, no tool-calling — in favour of plain Anthropic Messages
API calls (`anthropic.Anthropic().messages.create()`) further reduce time
and cost versus experiment1's batched-but-still-agentic approach?

Only the LLM interaction layer differs from experiment1:
1. No Claude Agent SDK, no `query()`, no `ClaudeAgentOptions`, no
   `fetch_wikipedia_summary` tool, no in-process MCP server. The Wikipedia
   lookup is called directly in Python for each species (same as the GBIF
   lookup already was) — deterministic, not agent-tool-mediated.
2. Both the batched per-species descriptions and the narrative guide are single
   plain `messages.create()` calls — one request in, one response out, no
   tool-use loop, no subprocess.
3. Plain synchronous calls throughout (no asyncio) — nothing in this pipeline
   runs concurrently, so there is no functional reason for async here; it was
   only present in experiment1 because the Agent SDK's query() is an async
   generator by necessity (it streams from a subprocess).
4. Usage stats are read from the Anthropic SDK's `response.usage` instead of
   the Agent SDK's `ResultMessage.usage`; there is no `total_cost_usd` or
   `duration_ms`/`duration_api_ms`/`num_turns` on a plain Messages API
   response, so cost is estimated from token counts (as experiment1's own
   fallback already did) and wall-clock time is measured directly around the
   call. num_turns is always 1 — there is no agent loop to count turns in.

Species list, GBIF lookup, Wikipedia summary logic, MODEL_PRICING, map
generation, --model CLI flag, and the summary table are otherwise identical to
experiment1, so results are directly comparable across all three prototypes.

Throwaway. Do not promote to production.

Requires ANTHROPIC_API_KEY in the environment. No `claude` CLI dependency.

Run:
  source venv/bin/activate && python prototypes/scripts/species_narrative_cost_experiment2.py \\
    --model claude-sonnet-5 2>&1 | tee prototypes/logs/narrative_cost_experiment2_$(date +%Y%m%d_%H%M%S).log

  source venv/bin/activate && python prototypes/scripts/species_narrative_cost_experiment2.py \\
    --model claude-haiku-4-5-20251001 2>&1 | tee prototypes/logs/narrative_cost_experiment2_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from urllib.parse import quote

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Approximate public per-MTok pricing, $/MTok — the plain Messages API has no
# total_cost_usd field, so this is the only cost source here (not a fallback).
MODEL_PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
DEFAULT_PRICING = (2.00, 10.00)

MAX_OUTPUT_TOKENS = 2048

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"

SPECIES_COLORS = ['#e41a1c', '#377eb8', '#4daf4a', '#ff7f00', '#984ea3']

# Captured from a live run of waypoint_spike.py (fetch_gbif -> select_species ->
# order_waypoints), Retiro Park, 2026 GBIF occurrences, top 5 by count, ordered by
# nearest-neighbour from park centre. Same snapshot as the other two prototypes.
HARDCODED_SPECIES = [
    {
        'species': 'Pica pica',
        'species_key': 5229490,
        'count': 81,
        'kingdom': 'Animalia',
        'hotspot_lat': 40.414847925925926,
        'hotspot_lon': -3.684565037037037,
    },
    {
        'species': 'Picus sharpei',
        'species_key': 9029556,
        'count': 63,
        'kingdom': 'Animalia',
        'hotspot_lat': 40.413754698412696,
        'hotspot_lon': -3.6842265714285714,
    },
    {
        'species': 'Alopochen aegyptiaca',
        'species_key': 2498252,
        'count': 49,
        'kingdom': 'Animalia',
        'hotspot_lat': 40.414394959183674,
        'hotspot_lon': -3.682108,
    },
    {
        'species': 'Cygnus atratus',
        'species_key': 2498344,
        'count': 43,
        'kingdom': 'Animalia',
        'hotspot_lat': 40.41386437209302,
        'hotspot_lon': -3.6816920930232557,
    },
    {
        'species': 'Anas platyrhynchos',
        'species_key': 9761484,
        'count': 53,
        'kingdom': 'Animalia',
        'hotspot_lat': 40.41556654716982,
        'hotspot_lon': -3.6832586792452826,
    },
]


def header(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


# ── Step A: GBIF common name lookup ─────────────────────────────

def gbif_common_name(species_key):
    if not species_key:
        return None
    resp = requests.get(
        f'https://api.gbif.org/v1/species/{species_key}/vernacularNames', timeout=15
    )
    if resp.status_code != 200:
        return None
    results = resp.json().get('results', [])
    english = [r['vernacularName'] for r in results if r.get('language') == 'eng']
    if english:
        return english[0]
    if results:
        return results[0].get('vernacularName')
    return None


# ── Step B: Wikipedia lookup — called directly in Python, no agent tool ─

def wikipedia_summary(title):
    url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}'
    resp = requests.get(url, timeout=15, headers={'User-Agent': 'nature-walker-prototype/0.1'})
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get('type') == 'disambiguation':
        return {'disambiguation': True, 'title': data.get('title')}
    extract = data.get('extract')
    if not extract:
        return None
    image = data.get('thumbnail', {}).get('source') or data.get('originalimage', {}).get('source')
    return {'title': data.get('title'), 'extract': extract, 'image_url': image}


def estimated_cost(input_tokens, output_tokens, model):
    in_price, out_price = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * in_price + \
           (output_tokens / 1_000_000) * out_price


def extract_usage_stats(response, model, elapsed_s):
    """Same shape as experiment1's extract_usage_stats, sourced from a plain
    Messages API response instead of an Agent SDK ResultMessage.

    There is no separate CLI-subprocess wall time vs API time here — a plain
    messages.create() call IS the API call — so elapsed_s and api_elapsed_s are
    the same measured value. num_turns is always 1: a single request/response,
    no agent loop. cost_usd is always estimated from tokens (no total_cost_usd
    on this API), using the same MODEL_PRICING table as experiment1.
    """
    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_creation_tokens = getattr(usage, 'cache_creation_input_tokens', 0) or 0
    cache_read_tokens = getattr(usage, 'cache_read_input_tokens', 0) or 0
    return {
        'elapsed_s': elapsed_s,
        'api_elapsed_s': elapsed_s,
        'num_turns': 1,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_creation_input_tokens': cache_creation_tokens,
        'cache_read_input_tokens': cache_read_tokens,
        'cost_usd': estimated_cost(input_tokens, output_tokens, model),
        'raw_usage': usage.model_dump(),
    }


# ── Step C: per-species descriptions — ONE batched plain message call ──
#
# Wikipedia extracts for all 5 species are fetched directly in Python first
# (deterministic, no tool-calling), then embedded in a single prompt so the
# model only has to write descriptions from supplied text — no agent loop,
# no tool-use round trip, one messages.create() call for all 5 species.

def run_batch_description_call(client, species_list, model):
    species_lines = []
    for i, sp in enumerate(species_list, 1):
        common_name = sp['common_name']
        lookup_title = common_name or sp['species']
        wiki = wikipedia_summary(lookup_title)
        if wiki is None or wiki.get('disambiguation'):
            wiki = wikipedia_summary(sp['species']) if common_name else None
        sp['image_url'] = wiki.get('image_url') if wiki else None
        sp['wiki_title'] = wiki.get('title') if wiki else None
        extract = wiki['extract'] if wiki else "(no Wikipedia article found)"

        species_lines.append(
            f"{i}. Scientific name: {sp['species']} | Common name: "
            f"{common_name or 'unknown'}\n   Wikipedia extract: {extract}"
        )
    species_block = "\n".join(species_lines)

    prompt = f"""Species to describe (in order), with a Wikipedia extract already
looked up for each:

{species_block}

For EACH species above, using ONLY the Wikipedia extract given, write a short,
factual, plain-language description (2-4 sentences) suitable for a species
identification card in a nature walk app. Do not use dramatic or adventurous
language — that comes later, in a separate narrative.

Respond with ONLY a JSON array (no markdown fences, no preamble) of exactly
{len(species_list)} objects in the same order as the list above, each shaped
like:
{{"index": <1-based index>, "description": "<2-4 sentence description>"}}"""

    print(f"\n  {DIM}--- PROMPT (batched description call, {len(species_list)} species) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.perf_counter() - start

    final_text = "".join(block.text for block in response.content if block.type == "text").strip()
    print(f"  {DIM}--- RESPONSE (batched description call) ---{RESET}")
    print(f"  {final_text}")

    descriptions_by_index = {}
    try:
        cleaned = re.sub(r'^```(json)?|```$', '', final_text, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        for item in parsed:
            descriptions_by_index[int(item['index'])] = item['description'].strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"  {RED}[warning] Failed to parse batched JSON response: {e}{RESET}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[turns={stats['num_turns']} wall={stats['elapsed_s']:.1f}s "
        f"api={stats['api_elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"cache_write={stats['cache_creation_input_tokens']} "
        f"cache_read={stats['cache_read_input_tokens']} out={stats['output_tokens']} "
        f"cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    for i, sp in enumerate(species_list):
        sp['description'] = descriptions_by_index.get(i + 1, "(no description generated)")

    return stats


# ── Step D: narrative guide — second plain message call, no tools ──
# Same prompt as experiment1, just issued via a plain messages.create() call.

def generate_narrative(client, enriched_species, model):
    lines = []
    for i, sp in enumerate(enriched_species, 1):
        name = sp['common_name'] or sp['species']
        lines.append(
            f"{i}. {name} ({sp['species']}) at "
            f"({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f}): {sp['description']}"
        )
    species_block = "\n".join(lines)

    prompt = f"""You are narrating a nature walk through Retiro Park, Madrid, in the
style of a David Attenborough nature documentary — full of wonder, adventure, and a
sense of discovery.

The walk visits {len(enriched_species)} species in this order, each at its own GPS
waypoint:

{species_block}

Write a single flowing narrative guide (roughly 250-400 words) for a walker
following this route, in visiting order. Weave in the species descriptions above
and a sense of place and journey — this is the only information available for now,
future versions may add more local detail. Write continuous narrated prose,
optionally broken into short paragraphs per waypoint. Do not use markdown headers
or a numbered list."""

    print(f"\n  {DIM}--- PROMPT (narrative call) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.perf_counter() - start

    narrative = "".join(block.text for block in response.content if block.type == "text").strip()
    print(f"  {DIM}--- RESPONSE (narrative call) ---{RESET}")
    print(f"  {narrative}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[turns={stats['num_turns']} wall={stats['elapsed_s']:.1f}s "
        f"api={stats['api_elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"cache_write={stats['cache_creation_input_tokens']} "
        f"cache_read={stats['cache_read_input_tokens']} out={stats['output_tokens']} "
        f"cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    return {
        'narrative': narrative,
        **stats,
    }


# ── Step E: map + narrative panel ────────────────────────────────
# Unchanged from experiment1 except the output filename and banner text, so
# this run doesn't overwrite the agentic experiment's artifact.

def generate_map(enriched_species, narrative_text, model):
    header("STEP: Generate Leaflet map with species cards + narrative panel")

    lats = [sp['hotspot_lat'] for sp in enriched_species]
    lons = [sp['hotspot_lon'] for sp in enriched_species]
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)

    marker_js = []
    for i, sp in enumerate(enriched_species):
        color = SPECIES_COLORS[i % len(SPECIES_COLORS)]
        num = i + 1
        name = (sp['common_name'] or sp['species']).replace("'", "\\'")
        sci_name = sp['species'].replace("'", "\\'")
        desc = sp['description'].replace("'", "\\'").replace("\n", " ")
        img_html = (
            f"<img src=\\'{sp['image_url']}\\' style=\\'width:100%;border-radius:4px;margin:4px 0\\'/>"
            if sp.get('image_url') else ""
        )

        marker_js.append(f"""
L.marker([{sp['hotspot_lat']},{sp['hotspot_lon']}], {{
  icon: L.divIcon({{
    html: '<div style="background:{color};color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,.4);border:2px solid white;">{num}</div>',
    iconSize:[32,32], iconAnchor:[16,16], className:''
  }})
}}).bindPopup(
  '<div style="max-width:220px">' +
  '<b>{num}. {name}</b><br><i style="color:#888;font-size:11px">{sci_name}</i>' +
  '{img_html}' +
  '<p style="font-size:12px;margin:6px 0 0">{desc}</p>' +
  '</div>'
).addTo(map);
""")

    coords = [[sp['hotspot_lat'], sp['hotspot_lon']] for sp in enriched_species]
    connector_js = (
        f"L.polyline({json.dumps(coords)},"
        f"{{color:'#333',weight:2.5,opacity:0.55,dashArray:'8,7'}}).addTo(map);"
    )

    narrative_html = narrative_text.replace("\n\n", "</p><p>").replace("\n", " ")

    model_slug = re.sub(r'[^a-z0-9]+', '_', model.lower()).strip('_')
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — species + narrative cost experiment 2, non-agentic ({model})</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{{margin:0;font-family:sans-serif}}
  #map{{height:100vh}}
  #banner{{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:1000;
    background:#ffd700;padding:3px 12px;border-radius:4px;font-size:11px;
    font-weight:bold;white-space:nowrap}}
  #narrative{{position:absolute;top:0;right:0;width:340px;height:100vh;z-index:1000;
    background:white;box-shadow:-2px 0 10px rgba(0,0,0,.2);overflow-y:auto;
    padding:20px;box-sizing:border-box}}
  #narrative h3{{margin-top:0;font-size:15px}}
  #narrative p{{font-size:13px;line-height:1.6;color:#333}}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — cost experiment 2: non-agentic plain message calls, model={model}</div>
<div id="map"></div>
<div id="narrative">
  <h3>Your Retiro Walk — A Nature Quest</h3>
  <p>{narrative_html}</p>
</div>
<script>
var map = L.map('map').setView([{center_lat},{center_lon}], 15);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'© OpenStreetMap contributors'
}}).addTo(map);
{connector_js}
{''.join(marker_js)}
</script>
</body>
</html>"""

    out = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'artifacts',
        f'retiro_narrative_map_cost_experiment2_{model_slug}.html',
    )
    with open(out, 'w') as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── Main ──────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--model', default='claude-sonnet-5',
        help='Model ID to pass to messages.create() (default: claude-sonnet-5). '
             'e.g. claude-haiku-4-5-20251001',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    model = args.model

    print(f"\n{BOLD}PROTOTYPE: Species Info + Narrative — Cost Experiment 2, non-agentic (model={model}){RESET}")
    print(f"{DIM}Question: does dropping the Agent SDK for plain Messages API calls further cut "
          f"time/cost versus experiment1's batched-but-agentic approach?{RESET}")

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    client = Anthropic()

    ordered = [dict(sp) for sp in HARDCODED_SPECIES]  # copy — we mutate per-species below

    header("STEP: GBIF common names")
    for sp in ordered:
        sp['common_name'] = gbif_common_name(sp['species_key'])
        label = sp['common_name'] or f"{DIM}(no common name found){RESET}"
        print(f"  {sp['species']:<40} -> {label}")

    header(f"STEP: Per-species descriptions (ONE batched plain message call, model={model})")
    batch_stats = run_batch_description_call(client, ordered, model)
    for sp in ordered:
        img_note = "image" if sp.get('image_url') else "no image"
        print(f"  {DIM}{sp['species']:<25} [{img_note}] {sp['description'][:80]}{RESET}")

    header("STEP: Narrative guide (combined)")
    narrative_result = generate_narrative(client, ordered, model)
    print(f"  {DIM}[{narrative_result['elapsed_s']:.1f}s, "
          f"{narrative_result['input_tokens']}in/{narrative_result['output_tokens']}out]{RESET}")

    map_path = generate_map(ordered, narrative_result['narrative'], model)

    all_stats = [batch_stats, narrative_result]

    def totals(field):
        return sum(r[field] for r in all_stats)

    header(f"SUMMARY — cost & timing (model={model}, non-agentic plain Messages API)")
    print(f"  {'Step':<28} {'Turns':>6} {'Wall':>7} {'API':>7} {'In':>6} "
          f"{'CacheW':>7} {'CacheR':>7} {'Out':>6} {'Cost':>8}")
    r = batch_stats
    print(f"  {'batched descriptions (x5)':<28} {r['num_turns']:>6} {r['elapsed_s']:>6.1f}s {r['api_elapsed_s']:>6.1f}s "
          f"{r['input_tokens']:>6} {r['cache_creation_input_tokens']:>7} "
          f"{r['cache_read_input_tokens']:>7} {r['output_tokens']:>6} ${r['cost_usd']:>7.4f}")
    r = narrative_result
    print(f"  {'narrative guide':<28} {r['num_turns']:>6} {r['elapsed_s']:>6.1f}s {r['api_elapsed_s']:>6.1f}s "
          f"{r['input_tokens']:>6} {r['cache_creation_input_tokens']:>7} "
          f"{r['cache_read_input_tokens']:>7} {r['output_tokens']:>6} ${r['cost_usd']:>7.4f}")
    print(f"  {'-'*93}")
    print(f"  {'TOTAL':<28} {totals('num_turns'):>6} {totals('elapsed_s'):>6.1f}s {totals('api_elapsed_s'):>6.1f}s "
          f"{totals('input_tokens'):>6} {totals('cache_creation_input_tokens'):>7} "
          f"{totals('cache_read_input_tokens'):>7} {totals('output_tokens'):>6} ${totals('cost_usd'):>7.4f}")
    print(f"\n  {DIM}Compare wall time and cost above against experiment1's batched-but-agentic run "
          f"(same model) to isolate the cost of the Agent SDK/CLI-subprocess layer itself.{RESET}")

    print(f"\n  {GREEN}Opening map -> {map_path}{RESET}\n")
    webbrowser.open(f'file://{map_path}')


if __name__ == '__main__':
    main()
