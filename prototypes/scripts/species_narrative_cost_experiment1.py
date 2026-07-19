#!/usr/bin/env python3
"""
PROTOTYPE — species_narrative_cost_experiment1.py
Cost-optimisation experiment forked from species_narrative_spike.py.

Question: does replacing 5 isolated per-species agent calls with a single
batched agent session (one query() call handling all 5 species) reduce the
cache-write overhead identified in that prototype's usage logs? And how does
swapping the model (via --model) change cost/time/accuracy?

Only two things differ from species_narrative_spike.py:
1. Per-species descriptions run as ONE long-lived agent session (single query()
   call, one prompt covering all 5 species, the agent calls the Wikipedia tool
   once per species and returns a single JSON array) instead of 5 separate
   isolated query() calls.
2. MODEL is a CLI argument (--model), not a hardcoded constant, so the same
   script can be re-run against a second model (e.g. claude-haiku-4-5-20251001)
   for comparison.

Narrative generation step is left as a second isolated call, unchanged from
species_narrative_spike.py, so the batching experiment isolates the one
variable being tested.

Throwaway. Do not promote to production.

Requires ANTHROPIC_API_KEY in the environment, and the `claude` CLI on PATH.

Run:
  source venv/bin/activate && python prototypes/scripts/species_narrative_cost_experiment1.py \\
    --model claude-sonnet-5 2>&1 | tee prototypes/logs/narrative_cost_experiment1_$(date +%Y%m%d_%H%M%S).log

  source venv/bin/activate && python prototypes/scripts/species_narrative_cost_experiment1.py \\
    --model claude-haiku-4-5-20251001 2>&1 | tee prototypes/logs/narrative_cost_experiment1_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import asyncio
import json
import os
import re
import sys
import webbrowser
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

load_dotenv()

# Approximate public per-MTok pricing, $/MTok — fallback only for when the SDK
# doesn't report total_cost_usd (it almost always does; see extract_usage_stats).
MODEL_PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
DEFAULT_PRICING = (2.00, 10.00)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"

SPECIES_COLORS = ['#e41a1c', '#377eb8', '#4daf4a', '#ff7f00', '#984ea3']

# Captured from a live run of waypoint_spike.py (fetch_gbif -> select_species ->
# order_waypoints), Retiro Park, 2026 GBIF occurrences, top 5 by count, ordered by
# nearest-neighbour from park centre. Same snapshot as species_narrative_spike.py.
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


# ── Step B: Wikipedia lookup, exposed to the agent as a tool ────

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


# Mutable, module-level: the tool has no return channel for anything but text,
# so side-channel data (Wikipedia image URLs) is captured here. Unlike the
# single-species-per-call original, the batched agent calls this tool once per
# species within ONE session, so this is now a list — one record appended per
# call, in call order. Queries run sequentially (never concurrently), so this
# is safe without locking.
_wiki_calls = []


@tool(
    "fetch_wikipedia_summary",
    "Fetch a Wikipedia page summary by title. Returns the article's opening "
    "extract, or a note if no article (or only a disambiguation page) was found.",
    {"query": str},
)
async def fetch_wikipedia_summary(args):
    title = args["query"]
    result = wikipedia_summary(title)
    if result is None:
        return {"content": [{"type": "text", "text": f"No Wikipedia article found for '{title}'."}]}
    if result.get("disambiguation"):
        return {
            "content": [{
                "type": "text",
                "text": f"'{title}' is a disambiguation page, not a specific article. "
                        f"Try a more specific title.",
            }]
        }
    _wiki_calls.append({
        'query': title,
        'wiki_title': result.get('title'),
        'image_url': result.get('image_url'),
    })
    return {"content": [{"type": "text", "text": result['extract']}]}


wikipedia_server = create_sdk_mcp_server(
    name="wikipedia",
    version="1.0.0",
    tools=[fetch_wikipedia_summary],
)


def extract_usage_stats(result_message, model):
    """Full breakdown of a ResultMessage — not just input/output tokens.

    input_tokens alone understates cost/volume: Claude Code's own system prompt
    is heavily prompt-cached, so most "input" shows up as cache_creation/
    cache_read tokens instead. total_cost_usd already accounts for all of this
    correctly; the raw_usage dict is included so nothing is hidden by our own
    field selection.
    """
    usage = (result_message.usage if result_message else None) or {}
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)
    cache_creation_tokens = usage.get('cache_creation_input_tokens', 0)
    cache_read_tokens = usage.get('cache_read_input_tokens', 0)
    cost_usd = (result_message.total_cost_usd if result_message else None)
    if cost_usd is None:
        cost_usd = estimated_cost(input_tokens, output_tokens, model)
    return {
        'elapsed_s': (result_message.duration_ms / 1000) if result_message else 0.0,
        'api_elapsed_s': (result_message.duration_api_ms / 1000) if result_message else 0.0,
        'num_turns': result_message.num_turns if result_message else 0,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_creation_input_tokens': cache_creation_tokens,
        'cache_read_input_tokens': cache_read_tokens,
        'cost_usd': cost_usd,
        'raw_usage': usage,
    }


# ── Step C: per-species descriptions — ONE batched agent session ─
#
# Instead of 5 isolated query() calls (species_narrative_spike.py), this is a
# single query() call covering all 5 species: the agent calls the Wikipedia
# tool once per species within the same session, then returns one JSON array.
# This is the variable under test — does one session's shared context reduce
# the ~2,000-token cache-write premium that recurred on every isolated call?

async def run_batch_description_agent(species_list, model):
    _wiki_calls.clear()

    species_lines = []
    for i, sp in enumerate(species_list, 1):
        common_name_hint = sp['common_name'] or "unknown — try the scientific name on Wikipedia"
        species_lines.append(
            f"{i}. Scientific name: {sp['species']} | Common name: {common_name_hint}"
        )
    species_block = "\n".join(species_lines)

    prompt = f"""Species to describe (in order):
{species_block}

For EACH species above, call the fetch_wikipedia_summary tool once (try the
common name first if given, otherwise the scientific name) to find information
about it. Then write a short, factual, plain-language description (2-4
sentences) for each, suitable for a species identification card in a nature
walk app. Do not use dramatic or adventurous language — that comes later, in a
separate narrative.

Once you have looked up all {len(species_list)} species, respond with ONLY a
JSON array (no markdown fences, no preamble) of exactly {len(species_list)}
objects in the same order as the list above, each shaped like:
{{"index": <1-based index>, "description": "<2-4 sentence description>"}}"""

    options = ClaudeAgentOptions(
        model=model,
        tools=[],  # no built-in filesystem/bash tools — isolated to the wiki lookup
        mcp_servers={"wikipedia": wikipedia_server},
        allowed_tools=["mcp__wikipedia__fetch_wikipedia_summary"],
        max_turns=len(species_list) * 2 + 2,
        effort="medium",
    )

    print(f"\n  {DIM}--- PROMPT (batched description session, {len(species_list)} species) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    final_text = ""
    result_message = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"  {YELLOW}[tool call] {block.name}({block.input}){RESET}")
                elif isinstance(block, TextBlock):
                    final_text = block.text.strip()
                    print(f"  {DIM}[agent text] {final_text}{RESET}")
        elif isinstance(message, ResultMessage):
            result_message = message

    print(f"  {DIM}--- RESPONSE (batched description session) ---{RESET}")
    print(f"  {final_text}")

    descriptions_by_index = {}
    try:
        cleaned = re.sub(r'^```(json)?|```$', '', final_text.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        for item in parsed:
            descriptions_by_index[int(item['index'])] = item['description'].strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"  {RED}[warning] Failed to parse batched JSON response: {e}{RESET}")

    stats = extract_usage_stats(result_message, model)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[turns={stats['num_turns']} wall={stats['elapsed_s']:.1f}s "
        f"api={stats['api_elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"cache_write={stats['cache_creation_input_tokens']} "
        f"cache_read={stats['cache_read_input_tokens']} out={stats['output_tokens']} "
        f"cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    # Wiki tool calls are recorded in call order; the prompt asks the agent to
    # look up species strictly in list order, so zip by position. This is a
    # simplifying assumption for a throwaway experiment, not a robust matcher.
    for i, sp in enumerate(species_list):
        sp['description'] = descriptions_by_index.get(i + 1, "(no description generated)")
        if i < len(_wiki_calls):
            sp['image_url'] = _wiki_calls[i].get('image_url')
            sp['wiki_title'] = _wiki_calls[i].get('wiki_title')
        else:
            sp['image_url'] = None
            sp['wiki_title'] = None

    return stats


# ── Step D: narrative guide — second isolated agent, no tools ───
# Unchanged from species_narrative_spike.py: kept isolated so the batching
# experiment above isolates the one variable being tested.

async def generate_narrative(enriched_species, model):
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

    options = ClaudeAgentOptions(
        model=model,
        tools=[],
        max_turns=1,
        effort="medium",
    )

    print(f"\n  {DIM}--- PROMPT (narrative agent) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    narrative = ""
    result_message = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"  {YELLOW}[tool call] {block.name}({block.input}){RESET}")
                elif isinstance(block, TextBlock):
                    narrative = block.text.strip()
        elif isinstance(message, ResultMessage):
            result_message = message

    print(f"  {DIM}--- RESPONSE (narrative agent) ---{RESET}")
    print(f"  {narrative}")

    stats = extract_usage_stats(result_message, model)
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

def estimated_cost(input_tokens, output_tokens, model):
    in_price, out_price = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * in_price + \
           (output_tokens / 1_000_000) * out_price


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
<title>PROTOTYPE — species + narrative cost experiment ({model})</title>
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
<div id="banner">PROTOTYPE — cost experiment: batched session, model={model}</div>
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
        f'retiro_narrative_map_cost_experiment_{model_slug}.html',
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
        help='Model ID to pass to ClaudeAgentOptions (default: claude-sonnet-5). '
             'e.g. claude-haiku-4-5-20251001',
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    model = args.model

    print(f"\n{BOLD}PROTOTYPE: Species Info + Narrative — Cost Experiment (model={model}){RESET}")
    print(f"{DIM}Question: does batching 5 species into one agent session cut cache-write cost "
          f"vs 5 isolated calls, and how does the model choice change cost/time/accuracy?{RESET}")

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    ordered = [dict(sp) for sp in HARDCODED_SPECIES]  # copy — we mutate per-species below

    header("STEP: GBIF common names")
    for sp in ordered:
        sp['common_name'] = gbif_common_name(sp['species_key'])
        label = sp['common_name'] or f"{DIM}(no common name found){RESET}"
        print(f"  {sp['species']:<40} -> {label}")

    header(f"STEP: Per-species descriptions (ONE batched session, model={model})")
    batch_stats = await run_batch_description_agent(ordered, model)
    for sp in ordered:
        img_note = "image" if sp.get('image_url') else "no image"
        print(f"  {DIM}{sp['species']:<25} [{img_note}] {sp['description'][:80]}{RESET}")

    header("STEP: Narrative guide (combined)")
    narrative_result = await generate_narrative(ordered, model)
    print(f"  {DIM}[{narrative_result['elapsed_s']:.1f}s, "
          f"{narrative_result['input_tokens']}in/{narrative_result['output_tokens']}out]{RESET}")

    map_path = generate_map(ordered, narrative_result['narrative'], model)

    all_stats = [batch_stats, narrative_result]

    def totals(field):
        return sum(r[field] for r in all_stats)

    header(f"SUMMARY — cost & timing (model={model}, batched session, via Claude Agent SDK)")
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
    print(f"\n  {DIM}Compare cache_creation_input_tokens above against the isolated-per-species run "
          f"in species_narrative_spike.py's logs (~2,000 tokens x 5 calls there).{RESET}")

    print(f"\n  {GREEN}Opening map -> {map_path}{RESET}\n")
    webbrowser.open(f'file://{map_path}')


if __name__ == '__main__':
    asyncio.run(main())
