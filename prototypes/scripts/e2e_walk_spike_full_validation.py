#!/usr/bin/env python3
"""
PROTOTYPE — e2e_walk_spike_full_validation.py
Brings together e2e_walk_spike_clustering.py (density-cluster hotspots,
user-drawn polygon support, GBIF scale guard/retry) and
e2e_walk_spike_server.py (common-name/Wikipedia enrichment, batched
description, structured narrative, quest-log map render) — then splits the
combined pipeline into two functions instead of one, so a caller (see
server_full_validation.py) can pause between them:

  resolve_species_query() — STEPS 1-5 (NL query -> intent -> resolve taxa ->
    fetch/rank with density-cluster hotspots -> nearest-neighbour waypoint
    ordering). Cheap: one LLM call + free GBIF calls. Returns a validation
    verdict alongside whatever species it found.
  run_pipeline()          — STEPS 6-8 (common name, Wikipedia, batched
    description, narrative, quest-log map render) given an already-resolved
    species list. Expensive: 2 more LLM calls, Wikipedia lookups.

Question: given the real pipeline's actual failure/edge shapes — empty or
unresolved taxon filters, fewer than 5 species found, overlapping waypoint
hotspots — does gating STEPS 6-8 behind an explicit user decision (for the
first two) or an automatic explanatory note (for the third) produce a UX
that never silently substitutes a different search than what the user asked
for? Full design rationale: this session's conversation, condensed into
planning_and_status_docs/WORK_SUMMARY_280726.md (to be written at session end).

Validation checkpoints (see resolve_species_query docstring for detail):
  - Case 1: no taxon filters produced, or all given filters failed
    species/match -> "needs_clarification", BEFORE any GBIF fetch.
  - Case 4: taxon filters resolved fine, but zero occurrences found in this
    area -> "needs_clarification".
  - Case 2: 1-4 species found (not enough for a full 5-stop walk) -> "ok"
    with a note, pipeline proceeds automatically (same search, just fewer
    results — nothing being substituted).
  - Case 3: two or more selected species' hotspots sit within
    MIN_WAYPOINT_SPACING_M of each other -> "ok" with a note, pipeline
    proceeds automatically (same species, just a rendering/data quirk this
    round only surfaces as an explanation, doesn't yet merge markers).

Requires ANTHROPIC_API_KEY in the environment.

Run standalone (CLI, for quick sanity checks outside the server):
  source venv/bin/activate && python prototypes/scripts/e2e_walk_spike_full_validation.py "Show me something colourful" \\
    2>&1 | tee prototypes/logs/e2e_walk_full_validation_$(date +%Y%m%d_%H%M%S).log
With a custom area: ... --polygon-file prototypes/reference/my_area.geojson
Force the case-1/4 fallback path directly: ... --override
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

DEFAULT_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON = 40.4153, -3.6844
YEAR_RANGE = "2023,2026"
TARGET_SPECIES_COUNT = 5
MIN_FUZZY_CONFIDENCE = 85
MAX_OUTPUT_TOKENS = 2048
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_GRID_N = 5
MIN_POINTS_TO_CLUSTER = 3  # <=2 points: clustering is meaningless, fall back to plain average
LARGE_RESULT_THRESHOLD = 1000  # above this, fall back to FALLBACK_YEAR instead of the full YEAR_RANGE
FALLBACK_YEAR = "2026"
MIN_WAYPOINT_SPACING_M = 20  # below this, two hotspots are treated as "the same spot" (case 3)

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

# ── Validation message copy (see this session's design conversation) ──────

CASE_1_MESSAGE = (
    "We couldn't match that to any specific species — would you like to try "
    "a different query, or see a walk with the most commonly observed "
    "species here instead?"
)
CASE_4_MESSAGE = (
    "We understood your request, but found no matching species in this "
    "area. Try a different query, or see a walk with the most commonly "
    "observed species here instead?"
)
CASE_RETRY_EMPTY_MESSAGE = (
    "Even the most commonly observed species search didn't turn up enough "
    "here — this might be a sparsely-recorded area. Try a different query "
    "or a different area."
)
CASE_2_NOTE = "We found fewer than 5 matching species in this area, so this walk has {count} stops instead of 5."
CASE_3_NOTE = (
    "Several of your selected species were observed in exactly the same "
    "place, so the walking spots aren't spread out. Look out for more of "
    "the species when you get there."
)


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


# ── Adaptive density-grid clustering (validated in e2e_walk_spike_clustering.py) ──

def cluster_species_hotspot(occurrence_points, grid_n=DEFAULT_GRID_N):
    lats = [p[0] for p in occurrence_points]
    lons = [p[1] for p in occurrence_points]
    avg_lat, avg_lon = sum(lats) / len(lats), sum(lons) / len(lons)

    if len(occurrence_points) < MIN_POINTS_TO_CLUSTER:
        return {"cluster_lat": avg_lat, "cluster_lon": avg_lon}

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_range, lon_range = max_lat - min_lat, max_lon - min_lon

    lat_step = lat_range / grid_n if lat_range > 0 else None
    lon_step = lon_range / grid_n if lon_range > 0 else None

    cells = defaultdict(list)
    for lat, lon in occurrence_points:
        row = min(int((lat - min_lat) / lat_step), grid_n - 1) if lat_step else 0
        col = min(int((lon - min_lon) / lon_step), grid_n - 1) if lon_step else 0
        cells[(row, col)].append((lat, lon))

    _, winning_points = max(cells.items(), key=lambda kv: len(kv[1]))
    cluster_lat = sum(p[0] for p in winning_points) / len(winning_points)
    cluster_lon = sum(p[1] for p in winning_points) / len(winning_points)

    return {"cluster_lat": cluster_lat, "cluster_lon": cluster_lon}


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


def find_overlapping_pairs(species, min_spacing_m=MIN_WAYPOINT_SPACING_M):
    """All-pairs check (not just consecutive route legs) — two species can sit
    close together without being visited back-to-back."""
    pairs = []
    for i in range(len(species)):
        for j in range(i + 1, len(species)):
            d = haversine_m(
                species[i]["hotspot_lat"], species[i]["hotspot_lon"],
                species[j]["hotspot_lat"], species[j]["hotspot_lon"],
            )
            if d < min_spacing_m:
                pairs.append((species[i]["species"], species[j]["species"], d))
    return pairs


# ── GeoJSON polygon-file input (pasted straight from latlong.net/polygon-drawer) ──

def geojson_polygon_to_wkt_and_center(geojson_obj):
    geometry = geojson_obj.get("geometry", geojson_obj)
    ring = geometry["coordinates"][0]

    points = [(pt[0], pt[1]) for pt in ring]  # (lon, lat)
    if points[0] != points[-1]:
        points.append(points[0])

    wkt_pairs = ", ".join(f"{lon} {lat}" for lon, lat in points)
    wkt = f"POLYGON(({wkt_pairs}))"

    center_lon = sum(p[0] for p in points) / len(points)
    center_lat = sum(p[1] for p in points) / len(points)
    return wkt, center_lat, center_lon


def load_polygon_file(path):
    with open(path) as f:
        geojson_obj = json.load(f)
    return geojson_polygon_to_wkt_and_center(geojson_obj)


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


# ── Step 1: LLM structured-output call — NL query -> taxonFilters/q ──────

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
        },
        "required": ["taxonFilters"],
    },
}
# NOTE: no "sort"/rarity field — deliberately removed. "Show me something
# rare" now gets the same treatment as any other non-taxon-mappable request
# (empty taxonFilters -> case 1 clarification), rather than the old
# rarest-first ranking (which surfaced singleton/1-observation records with
# no clustering signal — see WORK_SUMMARY_250726.md). Revisit once there's a
# real design for "rare" grounded in actual GBIF metadata (e.g. date-based
# signals), not before.


def run_query_generation_call(client, user_query, docs_summary, model):
    system_prompt = (
        "You turn a nature-walk request into a structured GBIF species "
        "query by calling the produce_gbif_query tool. Use the reference "
        "material below — it is the authoritative, verified guide for this "
        "task; do not rely on outside knowledge of the GBIF API.\n\n"
        f"{docs_summary}"
    )

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

def gbif_search(params, label, retries=2):
    for attempt in range(retries + 1):
        resp = requests.get("https://api.gbif.org/v1/occurrence/search", params=params, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and isinstance(data, dict):
            return data
        print(f"  {YELLOW}[retry] {label}: unexpected response (status={resp.status_code}, "
              f"type={type(data).__name__}), attempt {attempt + 1}/{retries + 1}{RESET}")
        time.sleep(1)
    return {"results": [], "endOfRecords": True, "count": 0}


def fetch_gbif_occurrences(polygon_wkt, extra_params, label="?"):
    probe_params = {
        "geometry": polygon_wkt, "year": YEAR_RANGE, "hasCoordinate": "true",
        "occurrenceStatus": "PRESENT", "limit": 0, **extra_params,
    }
    probe = gbif_search(probe_params, label)
    total = probe.get("count", 0)

    year_value = YEAR_RANGE
    if total > LARGE_RESULT_THRESHOLD:
        print(f"  {YELLOW}[scale guard] {label}: {total} occurrences under year={YEAR_RANGE} "
              f"(>{LARGE_RESULT_THRESHOLD}) — falling back to year={FALLBACK_YEAR}{RESET}")
        year_value = FALLBACK_YEAR

    results = []
    offset = 0
    while True:
        params = {
            "geometry": polygon_wkt,
            "year": year_value,
            "hasCoordinate": "true",
            "occurrenceStatus": "PRESENT",
            "limit": 300,
            "offset": offset,
            **extra_params,
        }
        data = gbif_search(params, label)
        page = data.get("results", [])
        results.extend(page)
        print(f"    {DIM}[fetch {label}] year={year_value} offset={offset} got={len(page)} running_total={len(results)}{RESET}")
        if data.get("endOfRecords", True):
            break
        offset += 300
    return results


def rank_species(occurrences, grid_n):
    by_species = defaultdict(list)
    for occ in occurrences:
        key = occ.get("species") or occ.get("scientificName")
        if key:
            by_species[key].append(occ)

    # Always most-observed-first — no "rarest" ordering (see QUERY_SCHEMA_TOOL note).
    ranked = sorted(by_species.items(), key=lambda x: len(x[1]), reverse=True)
    species_list = []
    for sp, recs in ranked:
        points = [
            (r["decimalLatitude"], r["decimalLongitude"])
            for r in recs if r.get("decimalLatitude") and r.get("decimalLongitude")
        ]
        if not points:
            continue
        cluster = cluster_species_hotspot(points, grid_n=grid_n)
        species_list.append({
            "species": sp,
            "species_key": recs[0].get("speciesKey"),
            "count": len(recs),
            "kingdom": recs[0].get("kingdom", "?"),
            "hotspot_lat": cluster["cluster_lat"],
            "hotspot_lon": cluster["cluster_lon"],
            "occurrence_points": points,
        })
    return species_list


def fetch_and_rank_group(taxon_filter, key, key_param, q, polygon_wkt, grid_n):
    extra_params = {key_param: key}
    if q:
        extra_params["q"] = q
    label = f"{taxon_filter['taxonRank']}/{taxon_filter['taxonValue']}"
    occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params, label=label)
    species_list = rank_species(occurrences, grid_n)
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


# ── Step: GBIF common name lookup ────────────────────────────────

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

    start = time.perf_counter()
    response = client.messages.create(model=model, max_tokens=MAX_OUTPUT_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    elapsed_s = time.perf_counter() - start

    final_text = "".join(b.text for b in response.content if b.type == "text").strip()

    descriptions_by_index = {}
    try:
        cleaned = re.sub(r"^```(json)?|```$", "", final_text, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        for item in parsed:
            descriptions_by_index[int(item["index"])] = item["description"].strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"  {RED}[warning] Failed to parse batched JSON response: {e}{RESET}")

    stats = extract_usage_stats(response, model, elapsed_s)
    print(
        f"  {DIM}[wall={stats['elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"out={stats['output_tokens']} cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    for i, sp in enumerate(species_list):
        sp["description"] = descriptions_by_index.get(i + 1, "(no description generated)")
    return stats


# ── Step: structured narrative (intro + per-waypoint) — plain message call ──

def generate_structured_narrative(client, ordered_species, model):
    lines = []
    for i, sp in enumerate(ordered_species, 1):
        name = sp["common_name"] or sp["species"]
        lines.append(
            f"{i}. {name} ({sp['species']}) at "
            f"({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f}): {sp['description']}"
        )
    species_block = "\n".join(lines)

    prompt = f"""You are narrating a nature walk in the style of a David
Attenborough nature documentary — full of wonder, adventure, and a sense of
discovery.

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

    start = time.perf_counter()
    response = client.messages.create(model=model, max_tokens=MAX_OUTPUT_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    elapsed_s = time.perf_counter() - start

    final_text = "".join(b.text for b in response.content if b.type == "text").strip()

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
    print(
        f"  {DIM}[wall={stats['elapsed_s']:.1f}s in={stats['input_tokens']} "
        f"out={stats['output_tokens']} cost=${stats['cost_usd']:.4f}]{RESET}"
    )

    waypoint_narratives = [
        narrative_by_index.get(i + 1, "(no narrative generated)")
        for i in range(len(ordered_species))
    ]
    return {"intro": intro or "(no intro generated)", "waypoints": waypoint_narratives, **stats}


# ── Step: render adventure-style "Quest Log" map (Variant A, ported, full
# interactivity), extended with an optional validation-notes banner ──────

def generate_quest_log_map(ordered_species, intro, waypoint_narratives, user_query, notes=None):
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
        points_js = json.dumps(sp.get("occurrence_points", []))
        species_js.append(
            "{num:%d,name:'%s',sci:'%s',color:'%s',lat:%r,lon:%r,img:'%s',desc:'%s',points:%s}"
            % (i + 1, name, sci, SPECIES_COLORS[i % len(SPECIES_COLORS)],
               sp["hotspot_lat"], sp["hotspot_lon"], img, desc, points_js)
        )
    species_array_js = "[\n  " + ",\n  ".join(species_js) + "\n]"

    narrative_js = json.dumps(waypoint_narratives)
    intro_js = json.dumps(intro)
    query_js = json.dumps(user_query)
    notes_js = json.dumps(notes or [])

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PROTOTYPE — full validation quest log</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  #app {{ position: relative; width: 100vw; height: 100vh; overflow: hidden; }}
  .map-fill {{ position: absolute; inset: 0; }}
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
  .qa-notes {{
    position: absolute; top: 70px; left: 50%; transform: translateX(-50%); z-index: 1000;
    background: rgba(139, 94, 24, .95); color: #fff3d6; padding: 10px 18px; border-radius: 8px;
    font-family: Georgia, serif; font-size: 13px; max-width: 70vw; text-align: center;
    box-shadow: 0 4px 12px rgba(0,0,0,.4); line-height: 1.5;
  }}
  .qa-notes div {{ margin-bottom: 4px; }}
  .qa-notes div:last-child {{ margin-bottom: 0; }}
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
</style>
</head>
<body>
<div id="app"></div>
<div id="run-banner">PROTOTYPE — full validation · query: {query_js.replace('"', '&quot;')}</div>
<script>
const SPECIES = {species_array_js};
const NARRATIVE_INTRO = {intro_js};
const NARRATIVE_BY_SPECIES = {narrative_js};
const NOTES = {notes_js};
const CENTER = [{center_lat}, {center_lon}];
const ROUTE_COORDS = SPECIES.map(s => [s.lat, s.lon]);

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
      <p style="font-size:11px;color:#7a5a2d;margin-top:10px">${{s.points.length}} recorded observations — see the small dots on the map for the full spread.</p>
    </div>
  `;
  backdrop.querySelector('.qa-close').onclick = () => backdrop.remove();
  backdrop.onclick = (e) => {{ if (e.target === backdrop) backdrop.remove(); }};
  document.body.appendChild(backdrop);
}}

function render() {{
  const container = document.getElementById('app');
  container.innerHTML = `
    <div class="qa-root">
      <div class="qa-titlebar">⚔ A Nature Quest</div>
      ${{NOTES.length ? `<div class="qa-notes">${{NOTES.map(n => `<div>${{n}}</div>`).join('')}}</div>` : ''}}
      <div class="qa-journal-tab" id="qa-tab">QUEST LOG</div>
      <div class="qa-journal" id="qa-journal">
        <h2>Quest Log</h2>
        <p style="font-size:13px;line-height:1.6;margin-bottom:16px">${{NARRATIVE_INTRO}}</p>
        ${{SPECIES.map((s, i) => `
          <details class="qa-entry" data-idx="${{i}}">
            <summary><span class="qa-num">${{s.num}}</span> ${{s.name}}</summary>
            <div class="qa-entry-body">${{NARRATIVE_BY_SPECIES[i]}}</div>
          </details>
        `).join('')}}
      </div>
      <div class="qa-map-wrap">
        <div id="qa-map" class="map-fill"></div>
      </div>
    </div>
  `;
  document.getElementById('qa-tab').onclick = () => document.getElementById('qa-journal').classList.toggle('open');

  const map = L.map('qa-map').setView(CENTER, 15);
  L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)',
    maxZoom: 17
  }}).addTo(map);
  L.polyline(ROUTE_COORDS, {{ color: '#333', weight: 2.5, opacity: 0.55, dashArray: '8,7' }}).addTo(map);

  let openDetail = null;
  SPECIES.forEach((s, i) => {{
    // Raw occurrence points for this species — built but not added to the
    // map until this species' marker is clicked (shows the full recorded
    // spread behind the single hotspot marker, not just the cluster itself).
    const detailLayer = L.layerGroup();
    s.points.forEach(p => {{
      L.circleMarker(p, {{ radius: 3, color: s.color, weight: 1, fillColor: s.color, fillOpacity: 0.7 }}).addTo(detailLayer);
    }});

    const icon = L.divIcon({{ html: `<div class="qa-marker-medallion">${{s.num}}</div>`, iconSize: [36, 36], iconAnchor: [18, 18], className: '' }});
    L.marker([s.lat, s.lon], {{ icon }}).addTo(map).on('click', () => {{
      if (openDetail === detailLayer) {{
        map.removeLayer(detailLayer);
        openDetail = null;
      }} else {{
        if (openDetail) map.removeLayer(openDetail);
        detailLayer.addTo(map);
        openDetail = detailLayer;
      }}
      openQuestModal(i);
    }});
  }});
}}

render();
</script>
</body>
</html>"""

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e2e_walk_full_validation_quest_log.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── resolve_species_query() — STEPS 1-5 + validation checkpoints ─────────

def resolve_species_query(user_query, polygon_wkt, center_lat, center_lon, grid_n=DEFAULT_GRID_N,
                           intent_model=DEFAULT_MODEL, override=False):
    """Returns {"status": "ok" | "needs_clarification", "message": str | None,
    "species": [...ordered species dicts...], "notes": [str, ...]}.

    `species` is [] only when status is "needs_clarification" with nothing to
    fall back on yet (case 1/4, before override). `notes` carries case 2/3
    explanations and always accompanies "ok" (never blocks).
    `override=True` skips STEP 1 entirely and forces an unfiltered,
    most_observed search — used when the user has already chosen "show me
    the most commonly observed species instead" after a case-1/4 prompt.
    """
    client = Anthropic()
    caches = load_reference_caches()

    if override:
        header("STEP 1: Skipped (override) — forcing unfiltered most_observed search")
        taxon_filters, q = [], None
    else:
        header(f"STEP 1: Generate structured GBIF query from natural-language request (model={intent_model})")
        docs_summary = load_docs_summary()
        query, _ = run_query_generation_call(client, user_query, docs_summary, intent_model)
        taxon_filters = query.get("taxonFilters", [])
        q = query.get("q")

    header("STEP 2: Resolve taxon filters to numeric GBIF keys")
    resolved, dropped = [], []
    if not taxon_filters:
        print(f"  {DIM}(no taxon filters requested){RESET}")
    for tf in taxon_filters:
        key = resolve_taxon_filter(tf, caches)
        if key is not None:
            resolved.append((tf, key, KEY_PARAM_BY_RANK[tf["taxonRank"]]))
        else:
            dropped.append(tf)

    no_signal = not taxon_filters
    all_unresolved = bool(taxon_filters) and not resolved

    if not override and (no_signal or all_unresolved):
        header("VALIDATION: needs_clarification — case 1 (no usable taxonomic signal)")
        print(f"  {YELLOW}{CASE_1_MESSAGE}{RESET}")
        return {"status": "needs_clarification", "message": CASE_1_MESSAGE, "species": [], "notes": []}

    header("STEP 3: Fetch + rank species per resolved filter (parallel)")
    if resolved:
        with ThreadPoolExecutor(max_workers=len(resolved)) as pool:
            futures = [pool.submit(fetch_and_rank_group, tf, key, key_param, q, polygon_wkt, grid_n)
                       for tf, key, key_param in resolved]
            group_results = [f.result() for f in futures]
    else:
        extra_params = {"q": q} if q else {}
        occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params, label="(default, no filter)")
        species_list = rank_species(occurrences, grid_n)
        print(f"  {'(default, no filter)':<30} {len(occurrences):>5} occurrences -> {len(species_list)} species")
        group_results = [("(default, no filter)", species_list)]

    header("STEP 4: Merge species across groups (quota/round-robin)")
    empty_groups = [label for label, species in group_results if not species]
    non_empty_groups = [species for label, species in group_results if species]
    if empty_groups:
        print(f"  {YELLOW}No species found for: {', '.join(empty_groups)} — redistributing their slots.{RESET}")
    if dropped:
        print(f"  {YELLOW}Dropped (unresolved) filters: "
              f"{', '.join(f'{tf['taxonRank']}/{tf['taxonValue']}' for tf in dropped)}{RESET}")

    if not non_empty_groups:
        header("VALIDATION: needs_clarification — case 4 (resolved filter(s), zero found here)"
               if not override else "VALIDATION: needs_clarification — retry-still-empty")
        message = CASE_RETRY_EMPTY_MESSAGE if override else CASE_4_MESSAGE
        print(f"  {YELLOW}{message}{RESET}")
        return {"status": "needs_clarification", "message": message, "species": [], "notes": []}

    selected = select_species_across_groups(non_empty_groups, target_total=TARGET_SPECIES_COUNT)
    for i, sp in enumerate(selected, 1):
        print(f"  {i}. {sp['species']:<40} {sp['count']:>4} obs  ({sp['kingdom']})")

    notes = []
    if len(selected) < TARGET_SPECIES_COUNT:
        note = CASE_2_NOTE.format(count=len(selected))
        notes.append(note)
        print(f"  {YELLOW}[note] {note}{RESET}")
    # Sub-group detail (which specific dropped/empty taxon groups underlie a
    # partial result) is deliberately NOT surfaced as its own note — target
    # users have no concept of "class/Crocodylia" and don't care which taxon
    # subdivision came up empty, only whether the walk itself came up short.
    # It's still logged above (STEP 4) for debugging.

    header("STEP 5: Order waypoints (nearest-neighbour from area centre)")
    ordered = order_waypoints(selected, center_lat, center_lon)

    overlap_pairs = find_overlapping_pairs(ordered, MIN_WAYPOINT_SPACING_M)
    if overlap_pairs:
        notes.append(CASE_3_NOTE)
        for a, b, d in overlap_pairs:
            print(f"  {YELLOW}[overlap] {a} <-> {b}: {d:.0f}m apart (< {MIN_WAYPOINT_SPACING_M}m threshold){RESET}")

    return {"status": "ok", "message": None, "species": ordered, "notes": notes}


# ── run_pipeline() — STEPS 6-8, given an already-resolved species list ───

def run_pipeline(species, user_query, description_model=DEFAULT_MODEL, narrative_model=DEFAULT_MODEL,
                  notes=None, open_browser=True):
    run_start = time.perf_counter()
    client = Anthropic()

    header(f"STEP 6: GBIF common name lookups ({len(species)} species)")
    for sp in species:
        sp["common_name"] = gbif_common_name(sp["species_key"])
        label = sp["common_name"] or f"{DIM}(no common name found){RESET}"
        print(f"  {sp['species']:<40} -> {label}")

    header(f"STEP 7: Per-species descriptions (batched plain message call, model={description_model})")
    description_stats = run_batch_description_call(client, species, description_model)

    header(f"STEP 8: Structured narrative — intro + per-waypoint (model={narrative_model})")
    narrative_result = generate_structured_narrative(client, species, narrative_model)

    map_path = generate_quest_log_map(species, narrative_result["intro"], narrative_result["waypoints"],
                                       user_query, notes=notes)

    total_elapsed_s = time.perf_counter() - run_start
    header("SUMMARY — time & cost")
    total_cost = description_stats["cost_usd"] + narrative_result["cost_usd"]
    print(f"  {'TOTAL wall time':<32} {total_elapsed_s:>6.1f}s")
    print(f"  {'TOTAL cost (LLM only)':<32} ${total_cost:>7.4f}")

    if open_browser:
        print(f"\n  {GREEN}Opening map -> {map_path}{RESET}\n")
        webbrowser.open(f"file://{map_path}")

    return {
        "species": species,
        "intro": narrative_result["intro"],
        "waypoints": narrative_result["waypoints"],
        "map_path": map_path,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language nature-walk request")
    parser.add_argument("--intent-model", default=DEFAULT_MODEL)
    parser.add_argument("--description-model", default=DEFAULT_MODEL)
    parser.add_argument("--narrative-model", default=DEFAULT_MODEL)
    parser.add_argument("--polygon-file", default=None,
                         help="GeoJSON Feature/Polygon file (as pasted from latlong.net/polygon-drawer). "
                              "Defaults to Retiro Park if omitted.")
    parser.add_argument("--grid-n", type=int, default=DEFAULT_GRID_N)
    parser.add_argument("--override", action="store_true",
                         help="Skip STEP 1, force the unfiltered most_observed fallback directly "
                              "(simulates clicking 'show most-observed instead').")
    return parser.parse_args()


def main():
    args = parse_args()
    print(f"\n{BOLD}PROTOTYPE: Full validation — resolve_species_query() + run_pipeline(){RESET}")
    print(f"{DIM}Question: does gating STEPS 6-8 behind an explicit user decision (cases 1/4) or an "
          f"automatic explanatory note (cases 2/3) stop the pipeline silently substituting a different "
          f"search than what the user asked for?{RESET}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    if args.polygon_file:
        polygon_wkt, center_lat, center_lon = load_polygon_file(args.polygon_file)
    else:
        polygon_wkt, center_lat, center_lon = DEFAULT_POLYGON, DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON
    print(f"{DIM}polygon-file={args.polygon_file or '(default: Retiro Park)'} center=({center_lat:.5f}, {center_lon:.5f}){RESET}")

    result = resolve_species_query(args.query, polygon_wkt, center_lat, center_lon,
                                    grid_n=args.grid_n, intent_model=args.intent_model, override=args.override)

    if result["status"] == "needs_clarification":
        header("PIPELINE STOPPED — awaiting user decision")
        print(f"  {BOLD}{result['message']}{RESET}")
        print(f"\n  {DIM}(in the real app: 'try a different query' or 're-run with --override'){RESET}")
        return

    if result["notes"]:
        header("NOTES (proceeding automatically)")
        for note in result["notes"]:
            print(f"  {YELLOW}{note}{RESET}")

    run_pipeline(result["species"], args.query, args.description_model, args.narrative_model,
                 notes=result["notes"])


if __name__ == "__main__":
    main()
