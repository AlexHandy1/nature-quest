#!/usr/bin/env python3
"""
PROTOTYPE — waypoint_spike.py
Question: Does a "5 species waypoints + dashed connector" approach feel more useful than a single OSM path?
Throwaway. Do not promote to production.

Run: source venv/bin/activate && python prototypes/waypoint_spike.py 2>&1 | tee prototypes/logs/waypoint_$(date +%Y%m%d_%H%M%S).log
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


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(df/2)**2 + math.cos(f1)*math.cos(f2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── Step 1: GBIF ──────────────────────────────────────────────

def fetch_gbif():
    header("STEP 1: Fetch GBIF occurrences (2026, Retiro polygon)")
    results = []
    offset = 0
    while True:
        params = {
            'geometry': GBIF_POLYGON, 'year': YEAR,
            'hasCoordinate': 'true', 'occurrenceStatus': 'PRESENT',
            'limit': 300, 'offset': offset,
        }
        print(f"  offset={offset} ...", end=" ", flush=True)
        resp = requests.get('https://api.gbif.org/v1/occurrence/search', params=params, timeout=30)
        data = resp.json()
        page = data.get('results', [])
        print(f"{len(page)} records")
        results.extend(page)
        if data.get('endOfRecords', True):
            break
        offset += 300
    print(f"\n  {BOLD}Total: {len(results)} records{RESET}")
    return results


# ── Step 2: Species selection ─────────────────────────────────

def select_species(occurrences):
    header("STEP 2: Species selection (top 5 by count)")
    by_species = defaultdict(list)
    for occ in occurrences:
        key = occ.get('species') or occ.get('scientificName')
        if key:
            by_species[key].append(occ)

    ranked = sorted(by_species.items(), key=lambda x: len(x[1]), reverse=True)
    selected = []
    for sp, recs in ranked[:5]:
        lats = [r['decimalLatitude'] for r in recs if r.get('decimalLatitude')]
        lons = [r['decimalLongitude'] for r in recs if r.get('decimalLongitude')]
        if not lats:
            continue
        selected.append({
            'species': sp,
            'count': len(recs),
            'kingdom': recs[0].get('kingdom', '?'),
            'hotspot_lat': sum(lats) / len(lats),
            'hotspot_lon': sum(lons) / len(lons),
            'records': recs,
        })
        print(f"  {sp:<50} {len(recs):>4} obs  "
              f"hotspot=({selected[-1]['hotspot_lat']:.4f}, {selected[-1]['hotspot_lon']:.4f})")

    return selected


# ── Step 3: Order waypoints (nearest-neighbour from centre) ───

def order_waypoints(species):
    header("STEP 3: Order waypoints (nearest-neighbour from park centre)")
    remaining = list(range(len(species)))
    ordered = []
    cur_lat, cur_lon = CENTER_LAT, CENTER_LON

    while remaining:
        nearest = min(
            remaining,
            key=lambda i: haversine_m(cur_lat, cur_lon,
                                      species[i]['hotspot_lat'],
                                      species[i]['hotspot_lon'])
        )
        ordered.append(nearest)
        remaining.remove(nearest)
        cur_lat = species[nearest]['hotspot_lat']
        cur_lon = species[nearest]['hotspot_lon']

    result = [species[i] for i in ordered]

    total_dist = 0
    for i in range(len(result) - 1):
        d = haversine_m(result[i]['hotspot_lat'], result[i]['hotspot_lon'],
                        result[i+1]['hotspot_lat'], result[i+1]['hotspot_lon'])
        total_dist += d
        print(f"  {i+1}→{i+2}  {result[i]['species']:<40} → {result[i+1]['species']:<40} {d:.0f}m")

    print(f"\n  {BOLD}Estimated total walk: {total_dist:.0f}m ({total_dist/1000:.2f}km){RESET}")
    return result


# ── Step 4: Map ───────────────────────────────────────────────

def generate_map(ordered_species):
    header("STEP 4: Generate Leaflet map — 5 waypoints + dashed connector")

    marker_js = []
    for i, sp in enumerate(ordered_species):
        color = SPECIES_COLORS[i]
        num = i + 1
        name = sp['species'].replace("'", "\\'")

        # Faint individual observation dots
        for r in sp['records']:
            rlat = r.get('decimalLatitude')
            rlon = r.get('decimalLongitude')
            if rlat and rlon:
                marker_js.append(
                    f"L.circleMarker([{rlat},{rlon}],"
                    f"{{radius:3,color:'{color}',fillColor:'{color}',"
                    f"fillOpacity:0.2,weight:0.5,interactive:false}}).addTo(map);"
                )

        # Numbered hotspot marker
        marker_js.append(f"""
L.marker([{sp['hotspot_lat']},{sp['hotspot_lon']}], {{
  icon: L.divIcon({{
    html: '<div style="background:{color};color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,.4);border:2px solid white;">{num}</div>',
    iconSize:[32,32], iconAnchor:[16,16], className:''
  }})
}}).bindPopup('<b>{num}. {name}</b><br>{sp['count']} observations<br><i>{sp['kingdom']}</i>').addTo(map);
""")

    # Dashed connector through hotspots in visit order
    coords = [[sp['hotspot_lat'], sp['hotspot_lon']] for sp in ordered_species]
    connector_js = (
        f"L.polyline({json.dumps(coords)},"
        f"{{color:'#333',weight:2.5,opacity:0.55,dashArray:'8,7'}}).addTo(map);"
    )

    legend_rows = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
        f'<div style="background:{SPECIES_COLORS[i]};color:white;border-radius:50%;'
        f'width:22px;height:22px;display:flex;align-items:center;justify-content:center;'
        f'font-weight:bold;font-size:11px;flex-shrink:0">{i+1}</div>'
        f'<span style="font-size:12px;line-height:1.3">{sp["species"]}<br>'
        f'<span style="color:#888;font-size:10px">{sp["count"]} obs · {sp["kingdom"]}</span></span></div>'
        for i, sp in enumerate(ordered_species)
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — Retiro waypoint spike</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{{margin:0;font-family:sans-serif}}
  #map{{height:100vh}}
  #legend{{position:absolute;bottom:20px;left:20px;z-index:1000;background:white;
    padding:14px 16px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.2);max-width:300px}}
  #legend h4{{margin:0 0 10px;font-size:13px;font-weight:bold}}
  #banner{{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:1000;
    background:#ffd700;padding:3px 12px;border-radius:4px;font-size:11px;
    font-weight:bold;white-space:nowrap}}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — waypoint approach (no OSM routing)</div>
<div id="map"></div>
<div id="legend">
  <h4>Suggested walk · Retiro 2026</h4>
  {legend_rows}
</div>
<script>
var map = L.map('map').setView([{CENTER_LAT},{CENTER_LON}], 15);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'© OpenStreetMap contributors'
}}).addTo(map);
{connector_js}
{''.join(marker_js)}
</script>
</body>
</html>"""

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'retiro_waypoint_map.html')
    with open(out, 'w') as f:
        f.write(html)
    print(f"  Written: {out}")
    return out


# ── Main ──────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}PROTOTYPE: Retiro Waypoint Spike{RESET}")
    print(f"{DIM}Question: Does 5-waypoint approach beat single OSM path?{RESET}")

    occurrences = fetch_gbif()
    if not occurrences:
        print(f"\n{RED}No occurrences — stopping.{RESET}")
        return

    species = select_species(occurrences)
    ordered = order_waypoints(species)
    map_path = generate_map(ordered)

    header("SUMMARY")
    print(f"  Waypoints in order:")
    for i, sp in enumerate(ordered):
        print(f"    {i+1}. {sp['species']} ({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f})")
    print(f"\n  {GREEN}Opening map → {map_path}{RESET}\n")
    webbrowser.open(f'file://{map_path}')


if __name__ == '__main__':
    main()
