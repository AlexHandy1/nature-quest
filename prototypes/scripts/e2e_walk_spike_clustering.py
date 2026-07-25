#!/usr/bin/env python3
"""
PROTOTYPE — e2e_walk_spike_clustering.py
Fork of e2e_walk_spike_polygon.py. Tests density-grid clustering as a
replacement for the plain average-of-occurrences species centroid. Strips
the per-species description/narrative LLM calls; keeps NL-query species
selection and polygon-area support. No unit tests (throwaway prototype).
Full design rationale: planning_and_status_docs/WORK_SUMMARY_250726.md.

Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/e2e_walk_spike_clustering.py "Today I want to learn about plants" 2>&1 | tee prototypes/logs/e2e_walk_clustering_$(date +%Y%m%d_%H%M%S).log
With a custom area: ... --polygon-file prototypes/reference/my_area.geojson
"""

import argparse
import json
import math
import os
import sys
import time
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# Same Retiro geometry every other prototype script hardcodes — kept here
# only as the CLI default so this script is still runnable standalone.
DEFAULT_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON = 40.4153, -3.6844
# Wider than the fixed-Retiro scripts' single YEAR=2026 — arbitrary drawn
# areas won't have Retiro's observation density, so a single year risks
# empty results. GBIF's occurrence/search accepts a comma range for `year`.
YEAR_RANGE = "2023,2026"
TARGET_SPECIES_COUNT = 5
MIN_FUZZY_CONFIDENCE = 85
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_GRID_N = 5
MIN_POINTS_TO_CLUSTER = 3  # <=2 points: clustering is meaningless, fall back to plain average

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


# ── New: adaptive density-grid clustering (the thing this prototype tests) ──

def cluster_species_hotspot(occurrence_points, grid_n=DEFAULT_GRID_N):
    """occurrence_points: list of (lat, lon) tuples for ONE species.
    Returns a dict with both the old plain-average centroid and the new
    density-cluster centroid, plus enough detail to log the trade-offs:
    bounding box, grid dims in degrees and meters, occupied-cell count, the
    winning cell's point count, and the distance between old and new.
    """
    lats = [p[0] for p in occurrence_points]
    lons = [p[1] for p in occurrence_points]
    avg_lat, avg_lon = sum(lats) / len(lats), sum(lons) / len(lons)

    if len(occurrence_points) < MIN_POINTS_TO_CLUSTER:
        return {
            "avg_lat": avg_lat, "avg_lon": avg_lon,
            "cluster_lat": avg_lat, "cluster_lon": avg_lon,
            "grid_n": None, "cells_occupied": None, "winning_cell_count": None,
            "cell_width_m": None, "cell_height_m": None,
            "distance_m": 0.0, "fallback_reason": "too_few_points",
        }

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_range, lon_range = max_lat - min_lat, max_lon - min_lon

    # Zero-range axis (e.g. all points share the same latitude): treat that
    # axis as a single bin rather than dividing by zero.
    lat_step = lat_range / grid_n if lat_range > 0 else None
    lon_step = lon_range / grid_n if lon_range > 0 else None

    cells = defaultdict(list)
    for lat, lon in occurrence_points:
        row = min(int((lat - min_lat) / lat_step), grid_n - 1) if lat_step else 0
        col = min(int((lon - min_lon) / lon_step), grid_n - 1) if lon_step else 0
        cells[(row, col)].append((lat, lon))

    winning_cell, winning_points = max(cells.items(), key=lambda kv: len(kv[1]))
    cluster_lat = sum(p[0] for p in winning_points) / len(winning_points)
    cluster_lon = sum(p[1] for p in winning_points) / len(winning_points)

    cell_width_m = haversine_m(min_lat, min_lon, min_lat, min_lon + lon_step) if lon_step else 0.0
    cell_height_m = haversine_m(min_lat, min_lon, min_lat + lat_step, min_lon) if lat_step else 0.0
    distance_m = haversine_m(avg_lat, avg_lon, cluster_lat, cluster_lon)

    return {
        "avg_lat": avg_lat, "avg_lon": avg_lon,
        "cluster_lat": cluster_lat, "cluster_lon": cluster_lon,
        "grid_n": grid_n, "cells_occupied": len(cells), "winning_cell_count": len(winning_points),
        "cell_width_m": cell_width_m, "cell_height_m": cell_height_m,
        "distance_m": distance_m, "fallback_reason": None,
        "bbox": (min_lat, min_lon, max_lat, max_lon),
    }


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


# ── GeoJSON polygon-file input (pasted straight from latlong.net/polygon-drawer) ──

def geojson_polygon_to_wkt_and_center(geojson_obj):
    """Accepts a GeoJSON Feature or bare Polygon geometry. GeoJSON coordinates
    are already [lon, lat] — the same order GBIF WKT wants — so no flip is
    needed (unlike Leaflet's [lat, lon] points in server_polygon.py's
    polygon_points_to_wkt()). Returns (wkt, center_lat, center_lon), where
    the center is a plain average of the polygon's own vertices (used only
    as the waypoint-ordering start point — a separate concern from the
    per-species density clustering this script exists to test)."""
    geometry = geojson_obj.get("geometry", geojson_obj)  # bare Polygon has no "geometry" wrapper
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


def rank_species(occurrences, sort, grid_n):
    """Unlike the polygon-fork version, this keeps every occurrence's raw
    (lat, lon) per species (for clustering + plotting raw points on the
    map), and computes BOTH the old plain-average hotspot and the new
    density-cluster hotspot via cluster_species_hotspot()."""
    by_species = defaultdict(list)
    for occ in occurrences:
        key = occ.get("species") or occ.get("scientificName")
        if key:
            by_species[key].append(occ)

    ranked = sorted(by_species.items(), key=lambda x: len(x[1]), reverse=(sort == "most_observed"))
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
            "occurrence_points": points,
            "hotspot_lat": cluster["cluster_lat"],
            "hotspot_lon": cluster["cluster_lon"],
            "avg_lat": cluster["avg_lat"],
            "avg_lon": cluster["avg_lon"],
            "cluster": cluster,
        })
    return species_list


def fetch_and_rank_group(taxon_filter, key, key_param, q, sort, polygon_wkt, grid_n):
    extra_params = {key_param: key}
    if q:
        extra_params["q"] = q
    occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params)
    species_list = rank_species(occurrences, sort, grid_n)
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


def log_cluster_comparison(species_list):
    header("Clustering trade-offs — old (average) vs. new (density cluster)")
    for sp in species_list:
        c = sp["cluster"]
        if c["fallback_reason"]:
            print(f"  {sp['species']:<40} {sp['count']:>4} obs  "
                  f"{YELLOW}fallback: {c['fallback_reason']} (avg used as-is){RESET}")
            continue
        print(f"  {sp['species']:<40} {sp['count']:>4} obs  grid={c['grid_n']}x{c['grid_n']} "
              f"cell≈{c['cell_width_m']:.0f}m×{c['cell_height_m']:.0f}m  "
              f"cells_occupied={c['cells_occupied']}  winning_cell_pts={c['winning_cell_count']}  "
              f"avg↔cluster={c['distance_m']:.0f}m")


# ── Step: GBIF common name lookup (deterministic — no Wikipedia/LLM description) ──

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


# ── Step: render comparison map — raw points, winning cell, old vs new marker ──

def generate_cluster_comparison_map(ordered_species, user_query, grid_n):
    header("STEP: Render clustering comparison map")

    lats = [sp["hotspot_lat"] for sp in ordered_species]
    lons = [sp["hotspot_lon"] for sp in ordered_species]
    center_lat, center_lon = sum(lats) / len(lats), sum(lons) / len(lons)

    species_js = []
    for i, sp in enumerate(ordered_species):
        name = (sp["common_name"] or sp["species"]).replace("'", "\\'")
        sci = sp["species"].replace("'", "\\'")
        color = SPECIES_COLORS[i % len(SPECIES_COLORS)]
        c = sp["cluster"]
        raw_points_js = json.dumps(sp["occurrence_points"])
        bbox_js = json.dumps(c.get("bbox"))
        species_js.append(
            "{num:%d,name:'%s',sci:'%s',color:'%s',count:%d,"
            "avgLat:%r,avgLon:%r,clusterLat:%r,clusterLon:%r,"
            "distanceM:%r,fallback:%s,bbox:%s,points:%s}"
            % (i + 1, name, sci, color, sp["count"],
               sp["avg_lat"], sp["avg_lon"], sp["hotspot_lat"], sp["hotspot_lon"],
               c["distance_m"], json.dumps(bool(c["fallback_reason"])), bbox_js, raw_points_js)
        )
    species_array_js = "[\n  " + ",\n  ".join(species_js) + "\n]"
    query_js = json.dumps(user_query)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PROTOTYPE — clustering vs. average centroid</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  #map {{ position: absolute; inset: 0; }}
  #banner {{
    position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 5000;
    background: #111; color: #fff; padding: 8px 16px; border-radius: 8px;
    font-size: 12px; max-width: 90vw; text-align: center;
  }}
  #legend {{
    position: fixed; bottom: 12px; left: 12px; z-index: 5000;
    background: #fff; padding: 10px 14px; border-radius: 8px; font-size: 12px;
    box-shadow: 0 2px 10px rgba(0,0,0,.3); line-height: 1.6;
  }}
  .swatch {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — clustering vs. average centroid · grid={grid_n}x{grid_n} · query: {query_js.replace('"', '&quot;')}</div>
<div id="map"></div>
<div id="legend">
  <div><span class="swatch" style="background:#999;border:2px solid #555"></span> old: plain average</div>
  <div><span class="swatch" style="background:#111"></span> new: density cluster</div>
  <div>small dots: raw occurrences · dashed box: winning grid cell</div>
</div>
<script>
const SPECIES = {species_array_js};
const CENTER = [{center_lat}, {center_lon}];
const ROUTE_COORDS = SPECIES.map(s => [s.clusterLat, s.clusterLon]);

const map = L.map('map').setView(CENTER, 15);
L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)',
  maxZoom: 17
}}).addTo(map);
L.polyline(ROUTE_COORDS, {{ color: '#333', weight: 2.5, opacity: 0.55, dashArray: '8,7' }}).addTo(map);

SPECIES.forEach(s => {{
  // Raw occurrence points
  s.points.forEach(p => {{
    L.circleMarker(p, {{ radius: 3, color: s.color, weight: 1, fillOpacity: 0.5 }}).addTo(map);
  }});

  // Winning grid cell bounds (if clustering ran, not a fallback)
  if (s.bbox && !s.fallback) {{
    const [minLat, minLon, maxLat, maxLon] = s.bbox;
    L.rectangle([[minLat, minLon], [maxLat, maxLon]], {{
      color: s.color, weight: 1, dashArray: '4,4', fillOpacity: 0
    }}).addTo(map);
  }}

  // Old marker: plain average
  L.circleMarker([s.avgLat, s.avgLon], {{
    radius: 8, color: '#555', weight: 2, fillColor: '#999', fillOpacity: 0.9
  }}).addTo(map).bindPopup(
    `<b>${{s.name}}</b> (${{s.sci}})<br>OLD: plain average<br>${{s.count}} total observations`
  );

  // New marker: density cluster (primary, numbered)
  const icon = L.divIcon({{
    html: `<div style="background:${{s.color}};color:#fff;border-radius:50%;width:28px;height:28px;` +
          `display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:13px;` +
          `border:2px solid #111;box-shadow:0 2px 6px rgba(0,0,0,.4)">${{s.num}}</div>`,
    iconSize: [28, 28], iconAnchor: [14, 14], className: ''
  }});
  L.marker([s.clusterLat, s.clusterLon], {{ icon }}).addTo(map).bindPopup(
    `<b>${{s.name}}</b> (${{s.sci}})<br>NEW: density cluster` +
    (s.fallback ? ' (fallback: too few points, same as average)' : `<br>moved ${{s.distanceM.toFixed(0)}}m from average`) +
    `<br>${{s.count}} total observations`
  );
}});
</script>
</body>
</html>"""

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "clustering_comparison_map.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── Main pipeline ────────────────────────────────────────────────

def run_pipeline(user_query, intent_model, polygon_wkt, center_lat, center_lon, grid_n, open_browser=True):
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
            futures = [pool.submit(fetch_and_rank_group, tf, key, key_param, q, sort, polygon_wkt, grid_n)
                       for tf, key, key_param in resolved]
            group_results = [f.result() for f in futures]
    else:
        extra_params = {"q": q} if q else {}
        occurrences = fetch_gbif_occurrences(polygon_wkt, extra_params)
        species_list = rank_species(occurrences, sort, grid_n)
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

    log_cluster_comparison(selected)

    header("STEP 5: Order waypoints (nearest-neighbour from drawn area's centre, using density-cluster hotspots)")
    ordered = order_waypoints(selected, center_lat, center_lon)

    header(f"STEP 6: GBIF common name lookups ({len(ordered)} species)")
    for sp in ordered:
        sp["common_name"] = gbif_common_name(sp["species_key"])
        label = sp["common_name"] or f"{DIM}(no common name found){RESET}"
        print(f"  {sp['species']:<40} -> {label}")

    map_path = generate_cluster_comparison_map(ordered, user_query, grid_n)

    total_elapsed_s = time.perf_counter() - run_start

    header("SUMMARY — time & cost")
    print(f"  {'Step':<32} {'Model':<28} {'Wall':>7} {'In':>6} {'Out':>6} {'Cost':>8}")
    print(f"  {'intent query generation':<32} {intent_model:<28} {intent_stats['elapsed_s']:>6.1f}s "
          f"{intent_stats['input_tokens']:>6} {intent_stats['output_tokens']:>6} ${intent_stats['cost_usd']:>7.4f}")
    print(f"  {'GBIF fetch (' + str(len(resolved) or 1) + ' parallel call(s))':<32} {'-':<28} {gbif_elapsed_s:>6.1f}s "
          f"{'-':>6} {'-':>6} {'$0.0000':>8}")
    print(f"  {'-'*93}")
    print(f"  {'TOTAL wall time':<32} {'':<28} {total_elapsed_s:>6.1f}s")
    print(f"  {'TOTAL cost (LLM only, GBIF free)':<32} {'':<28} {'':>7} {'':>6} {'':>6} ${intent_stats['cost_usd']:>7.4f}")

    if open_browser:
        print(f"\n  {GREEN}Opening map -> {map_path}{RESET}\n")
        webbrowser.open(f"file://{map_path}")

    return {"species": ordered, "map_path": map_path}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural-language nature-walk request, e.g. 'I want to learn about plants'")
    parser.add_argument("--intent-model", default=DEFAULT_MODEL, help="Model for NL -> structured GBIF query")
    parser.add_argument("--polygon-file", default=None,
                         help="Path to a GeoJSON Feature/Polygon file (as pasted from latlong.net/polygon-drawer). "
                              "Defaults to the Retiro Park polygon if omitted.")
    parser.add_argument("--grid-n", type=int, default=DEFAULT_GRID_N,
                         help=f"Adaptive grid resolution per species (NxN cells over that species' own occurrence "
                              f"bounding box). Default {DEFAULT_GRID_N}.")
    return parser.parse_args()


def main():
    args = parse_args()
    print(f"\n{BOLD}PROTOTYPE: Clustering vs. average centroid for species hotspot markers{RESET}")
    print(f"{DIM}Question: does an adaptive density-grid cluster represent \"where a walker "
          f"would find this species\" better than a plain average of all its occurrences?{RESET}")
    print(f"{DIM}intent-model={args.intent_model} grid-n={args.grid_n}{RESET}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"\n{RED}ANTHROPIC_API_KEY is not set — export it before running this script.{RESET}")
        sys.exit(1)

    if args.polygon_file:
        polygon_wkt, center_lat, center_lon = load_polygon_file(args.polygon_file)
        print(f"{DIM}polygon-file={args.polygon_file} -> center=({center_lat:.5f}, {center_lon:.5f}){RESET}")
    else:
        polygon_wkt, center_lat, center_lon = DEFAULT_POLYGON, DEFAULT_CENTER_LAT, DEFAULT_CENTER_LON
        print(f"{DIM}polygon=(default: Retiro Park) center=({center_lat}, {center_lon}){RESET}")

    run_pipeline(args.query, args.intent_model, polygon_wkt, center_lat, center_lon, args.grid_n)


if __name__ == "__main__":
    main()
