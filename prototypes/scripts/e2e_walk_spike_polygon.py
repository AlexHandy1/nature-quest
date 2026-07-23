#!/usr/bin/env python3
"""
PROTOTYPE — e2e_walk_spike_polygon.py
Fork of e2e_walk_spike_server.py (which stays untouched — see this
codebase's "copy, don't edit a validated checkpoint" convention). Only
difference: GBIF_POLYGON / CENTER_LAT / CENTER_LON are no longer hardcoded
module constants. run_pipeline() instead takes polygon_wkt + center_lat/
center_lon, so a caller can pass a user-drawn polygon instead of the fixed
Retiro Park one. CLI behaviour is preserved via a --polygon flag that
defaults to the same Retiro WKT used everywhere else in this codebase.

Question: does swapping the fixed Retiro polygon for an arbitrary
user-drawn one work end-to-end — real GBIF occurrence search against a
custom geometry, waypoint ordering from the drawn area's centroid (not a
hand-picked park centre), through to the same adventure-style quest-log map?
Throwaway. Do not promote to production.

Integrates, standalone (no cross-script imports, per this codebase's
established prototype convention — see PLANNING_INTENT_QUERY_210726.md §7
file layout rationale):
  1. intent_query_spike.py  — NL query -> structured GBIF query -> resolve
     taxa -> parallel occurrence/search -> quota/round-robin species merge.
     (Its per-filter GBIF fetch already computes each species' hotspot
     centroid — no separate waypoint GBIF fetch is needed.)
  2. waypoint_spike.py      — nearest-neighbour waypoint ordering from the
     fixed Retiro park centre, unaffected by which taxon group a species
     came from.
  3. species_narrative_cost_experiment2.py — GBIF common-name + Wikipedia
     lookups (deterministic), a batched per-species description call, and
     a narrative-guide call — both plain, non-agentic messages.create()
     calls. The narrative call here is changed to ask for structured JSON
     (intro + one paragraph per waypoint) instead of one flowing blob, to
     drive the quest-log's per-waypoint accordion.
  4. map_narrative_layout_prototype.html Variant A ("Quest Log" style) —
     ported as the sole map/narrative UI (no variant switcher), with full
     interactivity (journal toggle, click-to-open modal, mark-discovered),
     fed with real generated data instead of the hand-captured demo data.

Three independently-flagged models (--intent-model / --description-model /
--narrative-model), all defaulting to claude-haiku-4-5-20251001 — the model
shown in prior experiments (19-22 July sessions) to be decisively cheaper
and faster than Sonnet 5 for these call shapes, with no visible quality
loss on spot checks.

No unit tests for the newly-combined ordering/enrichment logic — pure
geometry (haversine/nearest-neighbour) and simple lookups, verified
visually via the rendered map, same as waypoint_spike.py itself (which
has no tests either). The deterministic taxon-resolution/validation/merge
logic this script duplicates from intent_query_spike.py is already unit
tested there (test_intent_query_spike.py).

Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/e2e_walk_spike_polygon.py "Today I want to learn about plants" 2>&1 | tee prototypes/logs/e2e_walk_polygon_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import json
import math
import os
import re
import sys
import time
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Same Retiro geometry every other prototype script hardcodes — kept here
# only as the CLI default so this script is still runnable standalone.
# The web path (server_polygon.py) always passes a caller-supplied polygon.
DEFAULT_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON = 40.4153, -3.6844
# A wider range than the fixed-Retiro scripts' single YEAR=2026 — arbitrary
# user-drawn areas won't have Retiro's observation density, so a single
# year risks empty results. GBIF's occurrence/search accepts a comma range
# for the year param (e.g. "2023,2026" = inclusive between those years).
YEAR_RANGE = "2023,2026"
TARGET_SPECIES_COUNT = 5
MIN_FUZZY_CONFIDENCE = 85
MAX_OUTPUT_TOKENS = 2048
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

MODEL_PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
DEFAULT_PRICING = (2.00, 10.00)

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")
SPECIES_COLORS = ["#e41a1c", "#377eb8", "#4daf4a", "#ff7f00", "#984ea3"]

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"


def header(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


def estimated_cost(input_tokens, output_tokens, model):
    in_price, out_price = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


def extract_usage_stats(response, model, elapsed_s):
    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
    return {
        "elapsed_s": elapsed_s,
        "num_turns": 1,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cost_usd": estimated_cost(input_tokens, output_tokens, model),
        "raw_usage": usage.model_dump(),
    }


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Deterministic logic (taxon resolution/validation/merge — unit tested in
# test_intent_query_spike.py; duplicated here per this codebase's standalone-
# prototype convention) ──────────────────────────────────────────

def resolve_from_local_cache(taxon_rank, taxon_value, caches):
    return caches.get(taxon_rank, {}).get(taxon_value)


def select_species_across_groups(groups, target_total=5):
    num_groups = len(groups)
    base_quota, remainder = divmod(target_total, num_groups)
    quotas = [base_quota + (1 if i < remainder else 0) for i in range(num_groups)]

    taken = [group[:quota] for group, quota in zip(groups, quotas)]
    shortfall = target_total - sum(len(t) for t in taken)

    while shortfall > 0:
        gave_any = False
        for i, group in enumerate(groups):
            if shortfall == 0:
                break
            available_extra = group[len(taken[i]):len(taken[i]) + 1]
            if available_extra:
                taken[i].extend(available_extra)
                shortfall -= 1
                gave_any = True
        if not gave_any:
            break

    selected = []
    for group_taken in taken:
        selected.extend(group_taken)
    return selected


def validate_species_match(response, requested_rank, min_fuzzy_confidence=85):
    match_type = response.get("matchType")
    is_accepted = match_type == "EXACT" or (
        match_type == "FUZZY" and response.get("confidence", 0) >= min_fuzzy_confidence
    )
    if is_accepted:
        return response.get(f"{requested_rank}Key")
    return None


def order_waypoints(species, center_lat, center_lon):
    remaining = list(range(len(species)))
    ordered = []
    cur_lat, cur_lon = center_lat, center_lon

    while remaining:
        nearest = min(
            remaining,
            key=lambda i: haversine_m(cur_lat, cur_lon, species[i]["hotspot_lat"], species[i]["hotspot_lon"]),
        )
        ordered.append(nearest)
        remaining.remove(nearest)
        cur_lat, cur_lon = species[nearest]["hotspot_lat"], species[nearest]["hotspot_lon"]

    result = [species[i] for i in ordered]
    total_dist = 0
    for i in range(len(result) - 1):
        d = haversine_m(result[i]["hotspot_lat"], result[i]["hotspot_lon"],
                         result[i + 1]["hotspot_lat"], result[i + 1]["hotspot_lon"])
        total_dist += d
        print(f"  {i+1}→{i+2}  {result[i]['species']:<35} → {result[i+1]['species']:<35} {d:.0f}m")
    print(f"\n  {BOLD}Estimated total walk: {total_dist:.0f}m ({total_dist/1000:.2f}km){RESET}")
    return result


# ── Reference material loading ──────────────────────────────────

def load_reference_caches():
    def load_json(filename):
        with open(os.path.join(REFERENCE_DIR, filename)) as f:
            return json.load(f)

    kingdom_map = load_json("gbif_kingdom_keys.json")
    class_map = load_json("gbif_common_class_keys.json")
    order_map = load_json("gbif_common_order_keys.json")
    for cache in (kingdom_map, class_map, order_map):
        cache.pop("_note", None)
    return {"kingdom": kingdom_map, "class": class_map, "order": order_map}


def load_docs_summary():
    with open(os.path.join(REFERENCE_DIR, "gbif_docs_summary.md")) as f:
        return f.read()


# ── Step 1: LLM structured-output call — NL query -> taxonFilters/q/sort ──

QUERY_SCHEMA_TOOL = {
    "name": "produce_gbif_query",
    "description": (
        "Translate the user's natural-language nature-walk request into a "
        "structured GBIF species query."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "taxonFilters": {
                "type": "array",
                "description": (
                    "Zero or more taxon filters. Each is a scientific rank + "
                    "name pair, never a numeric key. Empty list if the "
                    "request has no clear taxonomic signal."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "taxonRank": {
                            "type": "string",
                            "enum": ["kingdom", "phylum", "class", "order", "family", "genus"],
                        },
                        "taxonValue": {"type": "string"},
                    },
                    "required": ["taxonRank", "taxonValue"],
                },
            },
            "q": {
                "type": ["string", "null"],
                "description": (
                    "Free-text GBIF name search term. Name-like words only "
                    "(e.g. 'oak'). Never a qualitative/descriptive word "
                    "(colour, size, 'impressive'). Null if none applies."
                ),
            },
            "sort": {
                "type": "string",
                "enum": ["most_observed", "rarest"],
                "description": "Defaults to most_observed unless the request implies rarity.",
            },
        },
        "required": ["taxonFilters", "sort"],
    },
}


def run_query_generation_call(client, user_query, docs_summary, model):
    system_prompt = (
        "You turn a nature-walk request into a structured GBIF species "
        "query by calling the produce_gbif_query tool. Use the reference "
        "material below — it is the authoritative, verified guide for this "
        "task; do not rely on outside knowledge of the GBIF API.\n\n"
        f"{docs_summary}"
    )

    print(f"\n  {DIM}--- SYSTEM PROMPT (reference doc, {len(system_prompt)} chars) ---{RESET}")
    print(f"\n  {DIM}--- USER QUERY ---{RESET}")
    print(f"  {DIM}{user_query}{RESET}")

    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        tools=[QUERY_SCHEMA_TOOL],
        tool_choice={"type": "tool", "name": "produce_gbif_query"},
        messages=[{"role": "user", "content": user_query}],
    )
    elapsed_s = time.perf_counter() - start

    tool_use = next(b for b in response.content if b.type == "tool_use")
    print(f"\n  {DIM}--- STRUCTURED OUTPUT ---{RESET}")
    print(f"  {json.dumps(tool_use.input, indent=2)}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[wall={stats['elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"out={stats['output_tokens']} cost=${stats['cost_usd']:.4f}]{RESET}"
    )
    return tool_use.input, stats


# ── Step 2: resolve each taxonFilter to a numeric key ───────────

def resolve_taxon_filter(taxon_filter, caches):
    taxon_rank = taxon_filter["taxonRank"]
    taxon_value = taxon_filter["taxonValue"]

    cached_key = resolve_from_local_cache(taxon_rank, taxon_value, caches)
    if cached_key is not None:
        print(f"  {taxon_rank}/{taxon_value} -> {cached_key} {DIM}(local cache){RESET}")
        return cached_key

    resp = requests.get(
        "https://api.gbif.org/v1/species/match",
        params={"name": taxon_value, "rank": taxon_rank.upper()},
        timeout=15,
    ).json()
    key = validate_species_match(resp, requested_rank=taxon_rank, min_fuzzy_confidence=MIN_FUZZY_CONFIDENCE)
    if key is not None:
        print(f"  {taxon_rank}/{taxon_value} -> {key} {DIM}(live species/match, {resp.get('matchType')}){RESET}")
    else:
        print(f"  {YELLOW}{taxon_rank}/{taxon_value} -> unresolved (matchType={resp.get('matchType')}){RESET}")
    return key


# ── Step 3: fetch + rank species for one resolved filter ────────

def fetch_gbif_occurrences(polygon_wkt, extra_params):
    results = []
    offset = 0
    while True:
        params = {
            "geometry": polygon_wkt,
            "year": YEAR_RANGE,
            "hasCoordinate": "true",
            "occurrenceStatus": "PRESENT",
            "limit": 300,
            "offset": offset,
            **extra_params,
        }
        resp = requests.get("https://api.gbif.org/v1/occurrence/search", params=params, timeout=30)
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        if data.get("endOfRecords", True):
            break
        offset += 300
    return results


def rank_species(occurrences, sort):
    by_species = defaultdict(list)
    for occ in occurrences:
        key = occ.get("species") or occ.get("scientificName")
        if key:
            by_species[key].append(occ)

    ranked = sorted(by_species.items(), key=lambda x: len(x[1]), reverse=(sort == "most_observed"))
    species_list = []
    for sp, recs in ranked:
        lats = [r["decimalLatitude"] for r in recs if r.get("decimalLatitude")]
        lons = [r["decimalLongitude"] for r in recs if r.get("decimalLongitude")]
        if not lats:
            continue
        species_list.append({
            "species": sp,
            "species_key": recs[0].get("speciesKey"),
            "count": len(recs),
            "kingdom": recs[0].get("kingdom", "?"),
            "hotspot_lat": sum(lats) / len(lats),
            "hotspot_lon": sum(lons) / len(lons),
        })
    return species_list


def fetch_and_rank_group(taxon_filter, key, key_param, q, sort, polygon_wkt):
    extra_params = {key_param: key}
    if q:
        extra_params["q"] = q
    occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params)
    species_list = rank_species(occurrences, sort)
    label = f"{taxon_filter['taxonRank']}/{taxon_filter['taxonValue']}"
    print(f"  {label:<30} {len(occurrences):>5} occurrences -> {len(species_list)} species")
    return label, species_list


KEY_PARAM_BY_RANK = {
    "kingdom": "kingdomKey",
    "phylum": "phylumKey",
    "class": "classKey",
    "order": "orderKey",
    "family": "familyKey",
    "genus": "genusKey",
}


# ── Step: GBIF common name + Wikipedia lookups (deterministic) ──

def gbif_common_name(species_key):
    if not species_key:
        return None
    resp = requests.get(f"https://api.gbif.org/v1/species/{species_key}/vernacularNames", timeout=15)
    if resp.status_code != 200:
        return None
    results = resp.json().get("results", [])
    english = [r["vernacularName"] for r in results if r.get("language") == "eng"]
    if english:
        return english[0]
    if results:
        return results[0].get("vernacularName")
    return None


def wikipedia_summary(title):
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "nature-walker-prototype/0.1"})
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("type") == "disambiguation":
        return {"disambiguation": True, "title": data.get("title")}
    extract = data.get("extract")
    if not extract:
        return None
    image = data.get("thumbnail", {}).get("source") or data.get("originalimage", {}).get("source")
    return {"title": data.get("title"), "extract": extract, "image_url": image}


# ── Step: batched per-species description — ONE plain message call ──

def run_batch_description_call(client, species_list, model):
    species_lines = []
    for i, sp in enumerate(species_list, 1):
        common_name = sp["common_name"]
        lookup_title = common_name or sp["species"]
        wiki = wikipedia_summary(lookup_title)
        if wiki is None or wiki.get("disambiguation"):
            wiki = wikipedia_summary(sp["species"]) if common_name else None
        sp["image_url"] = wiki.get("image_url") if wiki else None
        extract = wiki["extract"] if wiki else "(no Wikipedia article found)"
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
    response = client.messages.create(model=model, max_tokens=MAX_OUTPUT_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    elapsed_s = time.perf_counter() - start

    final_text = "".join(b.text for b in response.content if b.type == "text").strip()
    print(f"  {DIM}--- RESPONSE (batched description call) ---{RESET}")
    print(f"  {final_text}")

    descriptions_by_index = {}
    try:
        cleaned = re.sub(r"^```(json)?|```$", "", final_text, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        for item in parsed:
            descriptions_by_index[int(item["index"])] = item["description"].strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"  {RED}[warning] Failed to parse batched JSON response: {e}{RESET}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[wall={stats['elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"out={stats['output_tokens']} cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    for i, sp in enumerate(species_list):
        sp["description"] = descriptions_by_index.get(i + 1, "(no description generated)")
    return stats


# ── Step: structured narrative (intro + per-waypoint) — plain message call ──
# Differs from species_narrative_cost_experiment2.py's narrative call: that
# one produces a single flowing blob; this asks for JSON (intro + one
# paragraph per waypoint) so the adventure-style quest-log's per-entry accordion has
# real generated text, not one blob repeated 5 times.

def generate_structured_narrative(client, ordered_species, model):
    lines = []
    for i, sp in enumerate(ordered_species, 1):
        name = sp["common_name"] or sp["species"]
        lines.append(
            f"{i}. {name} ({sp['species']}) at "
            f"({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f}): {sp['description']}"
        )
    species_block = "\n".join(lines)

    prompt = f"""You are narrating a nature walk through Retiro Park, Madrid, in the
style of a David Attenborough nature documentary — full of wonder, adventure, and
a sense of discovery.

The walk visits {len(ordered_species)} species in this order, each at its own GPS
waypoint:

{species_block}

Write:
- One scene-setting intro paragraph (2-4 sentences) that opens the walk, before
  the first species is reached.
- One narrated paragraph per waypoint (2-4 sentences each), in visiting order,
  weaving in that species' description above and a sense of place and journey.

Respond with ONLY a JSON object (no markdown fences, no preamble) shaped like:
{{"intro": "<intro paragraph>", "waypoints": [{{"index": <1-based index>, "narrative": "<paragraph>"}}, ...]}}
with exactly {len(ordered_species)} entries in "waypoints", in visiting order."""

    print(f"\n  {DIM}--- PROMPT (structured narrative call) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.perf_counter()
    response = client.messages.create(model=model, max_tokens=MAX_OUTPUT_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    elapsed_s = time.perf_counter() - start

    final_text = "".join(b.text for b in response.content if b.type == "text").strip()
    print(f"  {DIM}--- RESPONSE (structured narrative call) ---{RESET}")
    print(f"  {final_text}")

    intro = ""
    narrative_by_index = {}
    try:
        cleaned = re.sub(r"^```(json)?|```$", "", final_text, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        intro = parsed.get("intro", "").strip()
        for item in parsed.get("waypoints", []):
            narrative_by_index[int(item["index"])] = item["narrative"].strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"  {RED}[warning] Failed to parse structured narrative JSON: {e}{RESET}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(f"  {DIM}[raw usage] {stats['raw_usage']}{RESET}")
    print(
        f"  {DIM}[wall={stats['elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"out={stats['output_tokens']} cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    waypoint_narratives = [
        narrative_by_index.get(i + 1, "(no narrative generated)")
        for i in range(len(ordered_species))
    ]
    return {"intro": intro or "(no intro generated)", "waypoints": waypoint_narratives, **stats}


# ── Step: render adventure-style "Quest Log" map (Variant A, ported, full interactivity) ──

def generate_quest_log_map(ordered_species, intro, waypoint_narratives, user_query):
    header("STEP: Render adventure-style quest-log map (Variant A)")

    lats = [sp["hotspot_lat"] for sp in ordered_species]
    lons = [sp["hotspot_lon"] for sp in ordered_species]
    center_lat, center_lon = sum(lats) / len(lats), sum(lons) / len(lons)

    species_js = []
    for i, sp in enumerate(ordered_species):
        name = (sp["common_name"] or sp["species"]).replace("'", "\\'")
        sci = sp["species"].replace("'", "\\'")
        desc = sp["description"].replace("'", "\\'").replace("\n", " ")
        img = sp.get("image_url") or ""
        species_js.append(
            "{num:%d,name:'%s',sci:'%s',color:'%s',lat:%r,lon:%r,img:'%s',desc:'%s'}"
            % (i + 1, name, sci, SPECIES_COLORS[i % len(SPECIES_COLORS)],
               sp["hotspot_lat"], sp["hotspot_lon"], img, desc)
        )
    species_array_js = "[\n  " + ",\n  ".join(species_js) + "\n]"

    narrative_js = json.dumps(waypoint_narratives)
    intro_js = json.dumps(intro)
    query_js = json.dumps(user_query)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PROTOTYPE — e2e walk spike (adventure-style quest log)</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  #app {{ position: relative; width: 100vw; height: 100vh; overflow: hidden; }}
  .map-fill {{ position: absolute; inset: 0; }}
  .map-noise {{
    position: absolute; inset: 0; pointer-events: none; z-index: 450;
    background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="90" height="90"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"/></filter><rect width="100%" height="100%" filter="url(%23n)" opacity="0.35"/></svg>');
  }}
  #run-banner {{
    position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 5000;
    background: #111; color: #ffd700; padding: 6px 14px; border-radius: 999px;
    font-size: 11px; font-family: monospace; border: 2px solid #ffd700; max-width: 80vw;
    text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .qa-root {{ position: absolute; inset: 0; background: #1b1f18; }}
  .qa-titlebar {{
    position: absolute; top: 16px; left: 50%; transform: translateX(-50%); z-index: 1000;
    background: linear-gradient(#3a2f22, #2a2118); color: #e8d9b5; padding: 8px 24px;
    border: 2px solid #8a7346; border-radius: 6px; font-family: Georgia, serif;
    font-size: 16px; letter-spacing: 1px; box-shadow: 0 4px 12px rgba(0,0,0,.5);
  }}
  .qa-journal-tab {{
    position: absolute; top: 90px; left: 0; z-index: 1000; background: #4a3a26;
    color: #e8d9b5; padding: 14px 8px; border-radius: 0 8px 8px 0; cursor: pointer;
    font-family: Georgia, serif; writing-mode: vertical-rl; border: 2px solid #8a7346;
    border-left: none; box-shadow: 3px 3px 10px rgba(0,0,0,.4); font-size: 13px;
  }}
  .qa-journal {{
    position: absolute; top: 0; left: -380px; width: 380px; height: 100%; z-index: 999;
    background: #f3e6c8 url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4" fill="%23f3e6c8"/><circle cx="1" cy="1" r=".4" fill="%23e3d3ab"/></svg>');
    border-right: 4px solid #4a3a26; box-shadow: 4px 0 24px rgba(0,0,0,.5);
    transition: left .35s ease; overflow-y: auto; padding: 70px 20px 20px;
    font-family: Georgia, serif; color: #3a2f1f;
  }}
  .qa-journal.open {{ left: 0; }}
  .qa-journal h2 {{ font-size: 20px; border-bottom: 2px solid #8a7346; padding-bottom: 8px; }}
  .qa-entry {{ border: 1px solid #b8a06e; border-radius: 6px; margin-bottom: 10px; background: rgba(255,255,255,.35); }}
  .qa-entry summary {{
    padding: 10px 12px; cursor: pointer; font-weight: bold; display: flex;
    align-items: center; gap: 8px; list-style: none;
  }}
  .qa-entry summary::-webkit-details-marker {{ display: none; }}
  .qa-entry .qa-num {{
    width: 22px; height: 22px; border-radius: 50%; background: #4a3a26; color: #f3e6c8;
    display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0;
  }}
  .qa-entry .qa-check {{ margin-left: auto; color: #2f6f2f; font-weight: bold; }}
  .qa-entry-body {{ padding: 0 12px 12px 42px; font-size: 13.5px; line-height: 1.6; }}
  .qa-map-wrap {{
    position: absolute; inset: 14px; border-radius: 4px; overflow: hidden;
    box-shadow: 0 0 0 6px #2a2018, 0 0 0 10px #8a7346, 0 0 0 12px #3a2a15,
                inset 0 0 90px rgba(20,12,4,.75), 0 20px 50px rgba(0,0,0,.6);
  }}
  .qa-map-wrap .leaflet-tile-pane {{
    filter: sepia(.6) saturate(1.5) contrast(1.15) brightness(.82) hue-rotate(-8deg);
  }}
  .qa-map-vignette {{
    position: absolute; inset: 0; pointer-events: none; z-index: 460;
    background: radial-gradient(ellipse at center, rgba(58,42,21,0) 45%, rgba(30,20,8,.75) 100%);
    mix-blend-mode: multiply;
  }}
  .qa-map-grain {{ mix-blend-mode: overlay; opacity: .5; }}
  .qa-marker-medallion {{
    background: radial-gradient(circle at 35% 30%, #d9c28a, #7a5a2d 70%); color: #2a1f10;
    border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center;
    justify-content: center; font-weight: bold; font-size: 15px; font-family: Georgia, serif;
    border: 2px solid #3a2a15; box-shadow: 0 3px 8px rgba(0,0,0,.5);
  }}
  .qa-modal-backdrop {{
    position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 2000;
    display: flex; align-items: center; justify-content: center;
  }}
  .qa-modal {{
    width: 320px; background: #f3e6c8; border: 4px double #7a5a2d; border-radius: 4px;
    padding: 18px; font-family: Georgia, serif; color: #3a2f1f; position: relative;
    box-shadow: 0 12px 40px rgba(0,0,0,.6);
  }}
  .qa-modal img {{ width: 100%; border-radius: 4px; border: 2px solid #7a5a2d; margin: 8px 0; }}
  .qa-modal .qa-close {{ position: absolute; top: 8px; right: 10px; cursor: pointer; font-weight: bold; color: #7a5a2d; }}
  .qa-modal button.qa-discover {{
    margin-top: 10px; width: 100%; padding: 8px; background: #4a3a26; color: #f3e6c8;
    border: none; border-radius: 4px; cursor: pointer; font-family: Georgia, serif;
  }}
</style>
</head>
<body>
<div id="app"></div>
<div id="run-banner">PROTOTYPE — e2e walk spike · query: {query_js.replace('"', '&quot;')}</div>
<script>
const SPECIES = {species_array_js};
const NARRATIVE_INTRO = {intro_js};
const NARRATIVE_BY_SPECIES = {narrative_js};
const CENTER = [{center_lat}, {center_lon}];
const ROUTE_COORDS = SPECIES.map(s => [s.lat, s.lon]);

function baseTileLayer(map) {{
  L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)',
    maxZoom: 17
  }}).addTo(map);
  L.polyline(ROUTE_COORDS, {{ color: '#333', weight: 2.5, opacity: 0.55, dashArray: '8,7' }}).addTo(map);
}}

function openQuestModal(i) {{
  const s = SPECIES[i];
  const backdrop = document.createElement('div');
  backdrop.className = 'qa-modal-backdrop';
  backdrop.innerHTML = `
    <div class="qa-modal">
      <span class="qa-close">✕</span>
      <div style="font-size:11px;letter-spacing:1px;color:#7a5a2d">QUEST ENTRY ${{s.num}}/${{SPECIES.length}}</div>
      <h3 style="margin:4px 0 0">${{s.name}}</h3>
      <div style="font-style:italic;color:#7a5a2d;font-size:12px">${{s.sci}}</div>
      ${{s.img ? `<img src="${{s.img}}"/>` : ''}}
      <p style="font-size:13px;line-height:1.5">${{s.desc}}</p>
      <button class="qa-discover">Mark Discovered</button>
    </div>
  `;
  backdrop.querySelector('.qa-close').onclick = () => backdrop.remove();
  backdrop.onclick = (e) => {{ if (e.target === backdrop) backdrop.remove(); }};
  backdrop.querySelector('.qa-discover').onclick = () => {{
    const chk = document.querySelector(`[data-check="${{i}}"]`);
    if (chk) chk.style.visibility = 'visible';
    backdrop.remove();
  }};
  document.body.appendChild(backdrop);
}}

function render() {{
  const container = document.getElementById('app');
  container.innerHTML = `
    <div class="qa-root">
      <div class="qa-titlebar">⚔ Retiro Park — A Nature Quest</div>
      <div class="qa-journal-tab" id="qa-tab">QUEST LOG</div>
      <div class="qa-journal" id="qa-journal">
        <h2>Quest Log</h2>
        <p style="font-size:13px;line-height:1.6;margin-bottom:16px">${{NARRATIVE_INTRO}}</p>
        ${{SPECIES.map((s, i) => `
          <details class="qa-entry" data-idx="${{i}}">
            <summary><span class="qa-num">${{s.num}}</span> ${{s.name}} <span class="qa-check" data-check="${{i}}" style="visibility:hidden">✓</span></summary>
            <div class="qa-entry-body">${{NARRATIVE_BY_SPECIES[i]}}</div>
          </details>
        `).join('')}}
      </div>
      <div class="qa-map-wrap">
        <div id="qa-map" class="map-fill"></div>
        <div class="map-noise qa-map-grain"></div>
        <div class="qa-map-vignette"></div>
      </div>
    </div>
  `;
  document.getElementById('qa-tab').onclick = () => document.getElementById('qa-journal').classList.toggle('open');

  const map = L.map('qa-map').setView(CENTER, 15);
  baseTileLayer(map);
  SPECIES.forEach((s, i) => {{
    const icon = L.divIcon({{ html: `<div class="qa-marker-medallion">${{s.num}}</div>`, iconSize: [36, 36], iconAnchor: [18, 18], className: '' }});
    L.marker([s.lat, s.lon], {{ icon }}).addTo(map).on('click', () => openQuestModal(i));
  }});
}}

render();
</script>
</body>
</html>"""

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e2e_walk_quest_log.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── Main pipeline ────────────────────────────────────────────────

def run_pipeline(user_query, intent_model, description_model, narrative_model,
                  polygon_wkt=DEFAULT_POLYGON, center_lat=DEFAULT_CENTER_LAT,
                  center_lon=DEFAULT_CENTER_LON, open_browser=True):
    """Returns {"species": [...], "intro": str, "waypoints": [...], "map_path": str}
    on success, or None if no species were found for the query. `polygon_wkt` is the
    GBIF-format WKT geometry to search within; `center_lat`/`center_lon` is the point
    waypoint ordering starts from (the drawn area's centroid, when caller-supplied —
    not necessarily inside the polygon for odd/concave shapes, same caveat a
    hand-picked park centre would have). `open_browser=False` is used by
    server_polygon.py, which renders its own view from the returned data rather
    than opening the CLI's static artifact file."""
    run_start = time.perf_counter()
    client = Anthropic()

    header(f"STEP 1: Generate structured GBIF query from natural-language request (model={intent_model})")
    caches = load_reference_caches()
    docs_summary = load_docs_summary()
    query, intent_stats = run_query_generation_call(client, user_query, docs_summary, intent_model)

    taxon_filters = query.get("taxonFilters", [])
    q = query.get("q")
    sort = query.get("sort", "most_observed")

    header("STEP 2: Resolve taxon filters to numeric GBIF keys")
    resolved, dropped = [], []
    if not taxon_filters:
        print(f"  {DIM}(no taxon filters requested — default, unfiltered top {TARGET_SPECIES_COUNT}){RESET}")
    for tf in taxon_filters:
        key = resolve_taxon_filter(tf, caches)
        if key is not None:
            resolved.append((tf, key, KEY_PARAM_BY_RANK[tf["taxonRank"]]))
        else:
            dropped.append(tf)

    header("STEP 3: Fetch + rank species per resolved filter (parallel)")
    gbif_start = time.perf_counter()
    if resolved:
        with ThreadPoolExecutor(max_workers=len(resolved)) as pool:
            futures = [pool.submit(fetch_and_rank_group, tf, key, key_param, q, sort, polygon_wkt) for tf, key, key_param in resolved]
            group_results = [f.result() for f in futures]
    else:
        extra_params = {"q": q} if q else {}
        occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params)
        species_list = rank_species(occurrences, sort)
        print(f"  {'(default, no filter)':<30} {len(occurrences):>5} occurrences -> {len(species_list)} species")
        group_results = [("(default, no filter)", species_list)]
    gbif_elapsed_s = time.perf_counter() - gbif_start

    header("STEP 4: Merge species across groups (quota/round-robin)")
    empty_groups = [label for label, species in group_results if not species]
    non_empty_groups = [species for label, species in group_results if species]
    if empty_groups:
        print(f"  {YELLOW}No species found for: {', '.join(empty_groups)} — redistributing their slots.{RESET}")
    if dropped:
        print(f"  {YELLOW}Dropped (unresolved) filters: "
              f"{', '.join(f'{tf['taxonRank']}/{tf['taxonValue']}' for tf in dropped)}{RESET}")

    if not non_empty_groups:
        print(f"\n  {RED}No species found in any group — stopping.{RESET}")
        return None

    selected = select_species_across_groups(non_empty_groups, target_total=TARGET_SPECIES_COUNT)
    for i, sp in enumerate(selected, 1):
        print(f"  {i}. {sp['species']:<40} {sp['count']:>4} obs  ({sp['kingdom']})")

    header("STEP 5: Order waypoints (nearest-neighbour from drawn area's centre)")
    ordered = order_waypoints(selected, center_lat, center_lon)

    header(f"STEP 6: GBIF common name lookups ({len(ordered)} species)")
    for sp in ordered:
        sp["common_name"] = gbif_common_name(sp["species_key"])
        label = sp["common_name"] or f"{DIM}(no common name found){RESET}"
        print(f"  {sp['species']:<40} -> {label}")

    header(f"STEP 7: Per-species descriptions (batched plain message call, model={description_model})")
    description_stats = run_batch_description_call(client, ordered, description_model)

    header(f"STEP 8: Structured narrative — intro + per-waypoint (model={narrative_model})")
    narrative_result = generate_structured_narrative(client, ordered, narrative_model)

    map_path = generate_quest_log_map(ordered, narrative_result["intro"], narrative_result["waypoints"], user_query)

    total_elapsed_s = time.perf_counter() - run_start

    header("SUMMARY — time & cost")
    print(f"  {'Step':<32} {'Model':<28} {'Wall':>7} {'In':>6} {'Out':>6} {'Cost':>8}")
    llm_steps = [
        ("intent query generation", intent_model, intent_stats),
        ("batched descriptions", description_model, description_stats),
        ("structured narrative", narrative_model, narrative_result),
    ]
    total_cost = 0.0
    total_llm_wall = 0.0
    for label, model, stats in llm_steps:
        print(f"  {label:<32} {model:<28} {stats['elapsed_s']:>6.1f}s {stats['input_tokens']:>6} "
              f"{stats['output_tokens']:>6} ${stats['cost_usd']:>7.4f}")
        total_cost += stats["cost_usd"]
        total_llm_wall += stats["elapsed_s"]
    print(f"  {'GBIF fetch (' + str(len(resolved) or 1) + ' parallel call(s))':<32} {'-':<28} {gbif_elapsed_s:>6.1f}s "
          f"{'-':>6} {'-':>6} {'$0.0000':>8}")
    print(f"  {'-'*93}")
    print(f"  {'TOTAL wall time':<32} {'':<28} {total_elapsed_s:>6.1f}s")
    print(f"  {'TOTAL LLM wall time (3 calls)':<32} {'':<28} {total_llm_wall:>6.1f}s")
    print(f"  {'TOTAL cost (LLM only, GBIF/wiki free)':<32} {'':<28} {'':>7} {'':>6} {'':>6} ${total_cost:>7.4f}")

    if open_browser:
        print(f"\n  {GREEN}Opening map -> {map_path}{RESET}\n")
        webbrowser.open(f"file://{map_path}")

    return {
        "species": ordered,
        "intro": narrative_result["intro"],
        "waypoints": narrative_result["waypoints"],
        "map_path": map_path,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language nature-walk request, e.g. 'I want to learn about plants'")
    parser.add_argument("--intent-model", default=DEFAULT_MODEL, help="Model for NL -> structured GBIF query")
    parser.add_argument("--description-model", default=DEFAULT_MODEL, help="Model for batched per-species descriptions")
    parser.add_argument("--narrative-model", default=DEFAULT_MODEL, help="Model for the structured narrative guide")
    parser.add_argument("--polygon", default=DEFAULT_POLYGON, help="GBIF-format WKT polygon to search within (defaults to the Retiro Park polygon)")
    parser.add_argument("--center-lat", type=float, default=DEFAULT_CENTER_LAT, help="Latitude waypoint ordering starts from")
    parser.add_argument("--center-lon", type=float, default=DEFAULT_CENTER_LON, help="Longitude waypoint ordering starts from")
    return parser.parse_args()


def main():
    args = parse_args()
    print(f"\n{BOLD}PROTOTYPE: End-to-End Walk Spike — user-drawn polygon{RESET}")
    print(f"{DIM}Question: does swapping the fixed Retiro polygon for an arbitrary "
          f"user-drawn one work end-to-end, on Haiku?{RESET}")
    print(f"{DIM}intent-model={args.intent_model} description-model={args.description_model} "
          f"narrative-model={args.narrative_model}{RESET}")
    print(f"{DIM}polygon={args.polygon} center=({args.center_lat}, {args.center_lon}){RESET}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    run_pipeline(args.query, args.intent_model, args.description_model, args.narrative_model,
                 polygon_wkt=args.polygon, center_lat=args.center_lat, center_lon=args.center_lon)


if __name__ == "__main__":
    main()
