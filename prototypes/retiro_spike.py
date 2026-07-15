#!/usr/bin/env python3
"""
PROTOTYPE — retiro_spike.py
Question: Does the GBIF → species selection → OSM route → map pipeline hold up for Retiro Park?
Throwaway. Do not promote to production.

Run: source venv/bin/activate && python prototypes/retiro_spike.py
"""

import requests
import json
import webbrowser
import os
import math
from collections import defaultdict

GBIF_POLYGON = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
YEAR = 2026
CENTER_LAT, CENTER_LON = 40.4153, -3.6844

# OSM REST API bbox: lon_min,lat_min,lon_max,lat_max
OSM_BBOX = "-3.690,40.4076,-3.675,40.4216"
FOOTWAY_TYPES = {'footway', 'path', 'pedestrian'}

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

SPECIES_COLORS = ['#e41a1c', '#377eb8', '#4daf4a', '#ff7f00', '#984ea3']


def header(title):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


# ── Step 1: GBIF ──────────────────────────────────────────────

def fetch_gbif():
    header("STEP 1: Fetch GBIF occurrences (2026, Retiro polygon)")
    results = []
    offset = 0
    limit = 300

    while True:
        params = {
            'geometry': GBIF_POLYGON,
            'year': YEAR,
            'hasCoordinate': 'true',
            'occurrenceStatus': 'PRESENT',
            'limit': limit,
            'offset': offset,
        }
        print(f"  offset={offset} ...", end=" ", flush=True)
        resp = requests.get('https://api.gbif.org/v1/occurrence/search', params=params, timeout=30)
        data = resp.json()
        page = data.get('results', [])
        total = data.get('count', 0)
        print(f"{len(page)} records  (reported total: {total})")
        results.extend(page)
        if data.get('endOfRecords', True):
            break
        offset += limit

    print(f"\n  {BOLD}Total fetched:{RESET} {len(results)}")
    if results:
        s = results[0]
        print(f"  {DIM}Sample: {s.get('species', s.get('scientificName', '?'))} "
              f"at ({s.get('decimalLatitude')}, {s.get('decimalLongitude')}) "
              f"on {str(s.get('eventDate', '?'))[:10]}{RESET}")
    return results


# ── Step 2: Species selection ─────────────────────────────────

def select_species(occurrences):
    header("STEP 2: Species selection (top 5 by count)")
    by_species = defaultdict(list)
    no_species = 0
    for occ in occurrences:
        key = occ.get('species') or occ.get('scientificName')
        if key:
            by_species[key].append(occ)
        else:
            no_species += 1

    print(f"  Distinct species: {BOLD}{len(by_species)}{RESET}  "
          f"{DIM}({no_species} records had no species name){RESET}")

    ranked = sorted(by_species.items(), key=lambda x: len(x[1]), reverse=True)

    print(f"\n  {BOLD}Top 10:{RESET}")
    for i, (sp, recs) in enumerate(ranked[:10]):
        kingdom = recs[0].get('kingdom', '?')
        print(f"  {i+1:2}. {sp:<50} {len(recs):>4} obs  {DIM}[{kingdom}]{RESET}")

    selected = []
    for sp, recs in ranked[:5]:
        selected.append({
            'species': sp,
            'count': len(recs),
            'records': recs,
            'kingdom': recs[0].get('kingdom', '?'),
        })

    print(f"\n  {BOLD}Selected 5:{RESET}")
    for sp in selected:
        print(f"    {sp['species']}  ({sp['count']} obs, {sp['kingdom']})")

    return selected


# ── Step 3: OSM paths ─────────────────────────────────────────

def fetch_osm_paths():
    header("STEP 3: Fetch OSM walking paths (OSM REST API)")
    url = f"https://api.openstreetmap.org/api/0.6/map.json?bbox={OSM_BBOX}"
    print(f"  GET {url} ...", end=" ", flush=True)
    try:
        resp = requests.get(url, headers={'User-Agent': 'nature-walker-prototype/0.1'}, timeout=60)
        print(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"  {RED}Response: {resp.text[:300]}{RESET}")
            return []

        elements = resp.json().get('elements', [])
        nodes = {e['id']: e for e in elements if e['type'] == 'node'}
        raw_ways = [e for e in elements if e['type'] == 'way']

        footways = [
            w for w in raw_ways
            if w.get('tags', {}).get('highway') in FOOTWAY_TYPES
            and w.get('tags', {}).get('access') != 'private'
        ]

        # Build inline geometry from node lookup (same format score_paths expects)
        for w in footways:
            w['geometry'] = [
                {'lat': nodes[nid]['lat'], 'lon': nodes[nid]['lon']}
                for nid in w.get('nodes', [])
                if nid in nodes
            ]

        print(f"  {len(footways)} footways (from {len(raw_ways)} total ways, {len(nodes)} nodes)")
        if footways:
            print(f"  {DIM}Sample tags: {footways[0].get('tags', {})}{RESET}")
        else:
            print(f"  {RED}WARNING: No footways found{RESET}")
        return footways
    except Exception as e:
        print(f"\n  {RED}ERROR: {e}{RESET}")
        return []


# ── Step 4: Score paths ───────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(df/2)**2 + math.cos(f1)*math.cos(f2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def min_dist_to_way(lat, lon, geom):
    if not geom:
        return float('inf')
    return min(haversine_m(lat, lon, g['lat'], g['lon']) for g in geom)


def score_paths(ways, species, threshold_m=200):
    header(f"STEP 4: Score paths by species overlap (within {threshold_m}m)")
    scored = []
    for way in ways:
        geom = way.get('geometry', [])
        if not geom:
            continue
        covered = [
            sp['species']
            for sp in species
            if any(
                r.get('decimalLatitude') and
                min_dist_to_way(r['decimalLatitude'], r['decimalLongitude'], geom) < threshold_m
                for r in sp['records']
            )
        ]
        scored.append({'way': way, 'covered': covered, 'score': len(covered)})

    scored.sort(key=lambda x: x['score'], reverse=True)

    print(f"  Paths scored: {len(scored)}")
    print(f"\n  {BOLD}Top 5 paths:{RESET}")
    for i, s in enumerate(scored[:5]):
        tags = s['way'].get('tags', {})
        name = tags.get('name') or tags.get('highway', '?')
        print(f"  {i+1}. {name:<40} {s['score']}/5 species  {DIM}{s['covered']}{RESET}")

    if not scored:
        print(f"  {RED}No scorable paths found{RESET}")

    return scored


# ── Step 5: Map ───────────────────────────────────────────────

def generate_map(species, scored_paths):
    header("STEP 5: Generate Leaflet map")

    marker_js = []
    for i, sp in enumerate(species):
        color = SPECIES_COLORS[i]
        for r in sp['records']:
            lat = r.get('decimalLatitude')
            lon = r.get('decimalLongitude')
            if lat and lon:
                sp_name = sp['species'].replace("'", "\\'")
                date = str(r.get('eventDate', ''))[:10]
                marker_js.append(
                    f"L.circleMarker([{lat},{lon}],"
                    f"{{radius:5,color:'{color}',fillColor:'{color}',fillOpacity:0.7,weight:1}})"
                    f".bindPopup('{sp_name}<br><small>{date}</small>').addTo(map);"
                )

    path_js = []
    if scored_paths:
        best = scored_paths[0]
        coords = [[g['lat'], g['lon']] for g in best['way'].get('geometry', [])]
        if coords:
            path_js.append(
                f"L.polyline({json.dumps(coords)},"
                f"{{color:'#111',weight:5,opacity:0.85}})"
                f".bindPopup('Best path: {best['score']}/5 species').addTo(map);"
            )
        for s in scored_paths[1:]:
            coords = [[g['lat'], g['lon']] for g in s['way'].get('geometry', [])]
            if coords:
                path_js.append(
                    f"L.polyline({json.dumps(coords)},"
                    f"{{color:'#999',weight:1.5,opacity:0.4}}).addTo(map);"
                )

    legend_rows = "".join(
        f'<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
        f'background:{SPECIES_COLORS[i]};margin-right:6px"></span>'
        f'{sp["species"]} ({sp["count"]})</div>'
        for i, sp in enumerate(species)
    )

    best_score = scored_paths[0]['score'] if scored_paths else 0

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — Retiro data spike</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{{margin:0;font-family:sans-serif}}
  #map{{height:100vh}}
  #legend{{position:absolute;bottom:20px;left:20px;z-index:1000;background:white;
    padding:12px 16px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.2);
    font-size:12px;line-height:1.9;max-width:280px}}
  #legend h4{{margin:0 0 6px;font-size:13px}}
  #banner{{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:1000;
    background:#ffd700;padding:3px 12px;border-radius:4px;font-size:11px;font-weight:bold;
    white-space:nowrap}}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — throwaway data spike</div>
<div id="map"></div>
<div id="legend">
  <h4>Top 5 species · Retiro 2026</h4>
  {legend_rows}
  <div style="margin-top:8px;padding-top:8px;border-top:1px solid #eee">
    <span style="display:inline-block;width:20px;height:3px;background:#111;
      vertical-align:middle;margin-right:6px"></span>Best path ({best_score}/5 species)
  </div>
</div>
<script>
var map = L.map('map').setView([{CENTER_LAT},{CENTER_LON}], 15);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'© OpenStreetMap contributors'
}}).addTo(map);
{''.join(path_js)}
{''.join(marker_js)}
</script>
</body>
</html>"""

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'retiro_map.html')
    with open(out, 'w') as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── Main ──────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}PROTOTYPE: Retiro Walk Data Spike{RESET}")
    print(f"{DIM}Question: Does the GBIF → species selection → OSM path → map pipeline hold up?{RESET}")

    occurrences = fetch_gbif()
    if not occurrences:
        print(f"\n{RED}No occurrences — stopping.{RESET}")
        return

    species = select_species(occurrences)
    paths = fetch_osm_paths()
    scored = score_paths(paths, species) if paths else []
    map_path = generate_map(species, scored)

    header("SUMMARY")
    distinct = len(set(
        o.get('species') or o.get('scientificName')
        for o in occurrences
        if o.get('species') or o.get('scientificName')
    ))
    print(f"  GBIF records fetched : {len(occurrences)}")
    print(f"  Distinct species     : {distinct}")
    print(f"  OSM paths found      : {len(paths)}")
    print(f"  Best path coverage   : {scored[0]['score'] if scored else 0}/5 species")
    print(f"\n  {GREEN}Opening map → {map_path}{RESET}\n")
    webbrowser.open(f'file://{map_path}')


if __name__ == '__main__':
    main()
