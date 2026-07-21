#!/usr/bin/env python3
"""
PROTOTYPE — intent_query_spike.py
Question: can a natural-language walk request be turned into a valid GBIF
species query (via structured-output LLM call + local/live taxon
resolution) and produce a 5-species list?
Throwaway. Do not promote to production.

Scope for this round (see PLANNING_INTENT_QUERY_210726.md §1): stop at the
species list. No waypoint ordering, no map, no narrative — those are a
separate later integration step once this piece works in isolation.

Design: planning_and_status_docs/PLANNING_INTENT_QUERY_210726.md
Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/intent_query_spike.py "Today I want to learn about plants" 2>&1 | tee prototypes/logs/intent_query_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Same fixed location/year as waypoint_spike.py — location and year are out
# of scope for intent to influence in this round (PLANNING §1).
GBIF_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
YEAR = 2026
TARGET_SPECIES_COUNT = 5
MIN_FUZZY_CONFIDENCE = 85

# Approximate public per-MTok pricing, $/MTok — same table/pattern as
# species_narrative_cost_experiment2.py.
MODEL_PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
DEFAULT_PRICING = (2.00, 10.00)

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")

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


# ── Deterministic logic (unit tested — see test_intent_query_spike.py) ──

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
            break  # no group has any more species left to give, however short we are

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

    print(f"\n  {DIM}--- SYSTEM PROMPT (reference doc) ---{RESET}")
    print(f"  {DIM}{system_prompt}{RESET}")
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
        f"cache_write={stats['cache_creation_input_tokens']} "
        f"cache_read={stats['cache_read_input_tokens']} out={stats['output_tokens']} "
        f"cost=${stats['cost_usd']:.4f}]{RESET}"
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

def fetch_gbif_occurrences(extra_params):
    results = []
    offset = 0
    while True:
        params = {
            "geometry": GBIF_POLYGON,
            "year": YEAR,
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
            "count": len(recs),
            "kingdom": recs[0].get("kingdom", "?"),
            "hotspot_lat": sum(lats) / len(lats),
            "hotspot_lon": sum(lons) / len(lons),
        })
    return species_list


def fetch_and_rank_group(taxon_filter, key, key_param, q, sort):
    extra_params = {key_param: key}
    if q:
        extra_params["q"] = q
    occurrences = fetch_gbif_occurrences(extra_params)
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


# ── Main pipeline ────────────────────────────────────────────────

def run_pipeline(user_query, model):
    run_start = time.perf_counter()

    header("STEP 1: Generate structured GBIF query from natural-language request")
    client = Anthropic()
    caches = load_reference_caches()
    docs_summary = load_docs_summary()
    query, llm_stats = run_query_generation_call(client, user_query, docs_summary, model)

    taxon_filters = query.get("taxonFilters", [])
    q = query.get("q")
    sort = query.get("sort", "most_observed")

    header("STEP 2: Resolve taxon filters to numeric GBIF keys")
    resolved = []
    dropped = []
    if not taxon_filters:
        print(f"  {DIM}(no taxon filters requested — default, unfiltered top {TARGET_SPECIES_COUNT}){RESET}")
    for tf in taxon_filters:
        key = resolve_taxon_filter(tf, caches)
        if key is not None:
            resolved.append((tf, key, KEY_PARAM_BY_RANK[tf["taxonRank"]]))
        else:
            dropped.append(tf)

    if taxon_filters and not resolved:
        print(f"\n  {YELLOW}All requested taxon filters were unresolved — "
              f"falling back to the default, unfiltered top {TARGET_SPECIES_COUNT}.{RESET}")

    header("STEP 3: Fetch + rank species per resolved filter (parallel)")
    gbif_start = time.perf_counter()
    if resolved:
        with ThreadPoolExecutor(max_workers=len(resolved)) as pool:
            futures = [
                pool.submit(fetch_and_rank_group, tf, key, key_param, q, sort)
                for tf, key, key_param in resolved
            ]
            group_results = [f.result() for f in futures]
    else:
        extra_params = {"q": q} if q else {}
        occurrences = fetch_gbif_occurrences(extra_params)
        species_list = rank_species(occurrences, sort)
        print(f"  {'(default, no filter)':<30} {len(occurrences):>5} occurrences -> {len(species_list)} species")
        group_results = [("(default, no filter)", species_list)]
    gbif_elapsed_s = time.perf_counter() - gbif_start

    header("STEP 4: Merge species across groups (quota/round-robin)")
    empty_groups = [label for label, species in group_results if not species]
    non_empty_groups = [species for label, species in group_results if species]

    if empty_groups:
        print(f"  {YELLOW}No species found for: {', '.join(empty_groups)} — "
              f"redistributing their slots to the other groups.{RESET}")

    if not non_empty_groups:
        print(f"  {RED}No species found in any group.{RESET}")
        selected = []
    else:
        selected = select_species_across_groups(non_empty_groups, target_total=TARGET_SPECIES_COUNT)

    header("RESULT")
    if dropped:
        print(f"  {YELLOW}Dropped (unresolved) filters: "
              f"{', '.join(f'{tf['taxonRank']}/{tf['taxonValue']}' for tf in dropped)}{RESET}")
    for i, sp in enumerate(selected, 1):
        print(f"  {i}. {sp['species']:<40} {sp['count']:>4} obs  ({sp['kingdom']})  "
              f"({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f})")
    if not selected:
        print(f"  {RED}(no species selected){RESET}")

    total_elapsed_s = time.perf_counter() - run_start

    header(f"SUMMARY — time & cost (model={model})")
    print(f"  {'Step':<28} {'Wall':>7} {'In':>6} {'CacheW':>7} {'CacheR':>7} {'Out':>6} {'Cost':>8}")
    r = llm_stats
    print(f"  {'query generation (LLM)':<28} {r['elapsed_s']:>6.1f}s {r['input_tokens']:>6} "
          f"{r['cache_creation_input_tokens']:>7} {r['cache_read_input_tokens']:>7} "
          f"{r['output_tokens']:>6} ${r['cost_usd']:>7.4f}")
    print(f"  {'GBIF fetch (' + str(len(resolved) or 1) + ' parallel call(s))':<28} {gbif_elapsed_s:>6.1f}s "
          f"{'-':>6} {'-':>7} {'-':>7} {'-':>6} {'$0.0000':>8}")
    print(f"  {'-'*73}")
    print(f"  {'TOTAL wall time':<28} {total_elapsed_s:>6.1f}s")
    print(f"  {'TOTAL cost (LLM only, GBIF is free)':<28} ${r['cost_usd']:>7.4f}")

    return selected


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language nature-walk request, e.g. 'I want to learn about plants'")
    parser.add_argument("--model", default="claude-sonnet-5", help="Model ID for the query-generation call")
    return parser.parse_args()


def main():
    args = parse_args()

    print(f"\n{BOLD}PROTOTYPE: Intent Query Spike (model={args.model}){RESET}")
    print(f"{DIM}Question: can a natural-language request become a valid GBIF species query?{RESET}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    run_pipeline(args.query, args.model)


if __name__ == "__main__":
    main()
