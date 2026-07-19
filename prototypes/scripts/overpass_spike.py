#!/usr/bin/env python3
"""
PROTOTYPE — overpass_spike.py
Isolated test: can we get OSM footpaths for Retiro Park from the OSM REST API?
Throwaway.

Run: source venv/bin/activate && python prototypes/scripts/overpass_spike.py
"""

import requests

# bbox: left, bottom, right, top (lon_min, lat_min, lon_max, lat_max)
BBOX = "-3.690,40.4076,-3.675,40.4216"
FOOTWAY_TYPES = {'footway', 'path', 'pedestrian'}

URL = f"https://api.openstreetmap.org/api/0.6/map.json?bbox={BBOX}"


if __name__ == '__main__':
    print(f"OSM REST API spike — Retiro footpaths")
    print(f"URL: {URL}\n")

    print("Fetching ...", end=" ", flush=True)
    resp = requests.get(URL, headers={'User-Agent': 'nature-walker-prototype/0.1'}, timeout=60)
    print(f"HTTP {resp.status_code}")

    if resp.status_code != 200:
        print(f"Error: {resp.text[:300]}")
        exit(1)

    data = resp.json()
    elements = data.get('elements', [])
    print(f"Total elements returned: {len(elements)}")

    ways = [e for e in elements if e['type'] == 'way']
    nodes = {e['id']: e for e in elements if e['type'] == 'node'}
    print(f"Ways: {len(ways)}  Nodes: {len(nodes)}")

    footways = [
        w for w in ways
        if w.get('tags', {}).get('highway') in FOOTWAY_TYPES
        and w.get('tags', {}).get('access') != 'private'
    ]
    print(f"Footways/paths after filter: {len(footways)}")

    if footways:
        sample = footways[0]
        print(f"\nSample tags: {sample.get('tags', {})}")
        sample_nodes = sample.get('nodes', [])[:3]
        print(f"Sample node IDs: {sample_nodes}")
        for nid in sample_nodes:
            n = nodes.get(nid, {})
            print(f"  node {nid}: lat={n.get('lat')}, lon={n.get('lon')}")
    else:
        print("No footways found.")
