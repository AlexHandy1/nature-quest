#!/usr/bin/env python3
"""
PROTOTYPE — osm_data_explorer.py
Question: What does OSM actually return for the Retiro Park bbox beyond footways?
Throwaway. Do not promote to production.

Run: source venv/bin/activate && python prototypes/osm_data_explorer.py 2>&1 | tee prototypes/logs/osm_explorer_$(date +%Y%m%d_%H%M%S).log
"""

import requests
import json
import sys
from collections import defaultdict

OSM_BBOX = "-3.690,40.4076,-3.675,40.4216"  # default: Retiro Park

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"



def header(title):
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")


def subheader(title):
    print(f"\n{CYAN}  ── {title} ──{RESET}")


def fetch_osm():
    header("Fetching all OSM elements for Retiro Park bbox")
    url = f"https://api.openstreetmap.org/api/0.6/map.json?bbox={OSM_BBOX}"
    print(f"  GET {url}")
    print(f"  bbox: {OSM_BBOX}\n")
    resp = requests.get(url, headers={'User-Agent': 'nature-walker-prototype/0.1'}, timeout=60)
    print(f"  HTTP {resp.status_code}  ({len(resp.content):,} bytes)")
    resp.raise_for_status()
    return resp.json().get('elements', [])


def summarise_element_types(elements):
    header("Element type breakdown")
    by_type = defaultdict(list)
    for e in elements:
        by_type[e['type']].append(e)

    for etype, items in sorted(by_type.items()):
        tagged = [e for e in items if e.get('tags')]
        print(f"  {BOLD}{etype:<12}{RESET}  total={len(items):>6}   with tags={len(tagged):>6}")

    return by_type


def summarise_ways(ways):
    header("WAY tag keys — all keys ranked by frequency")

    untagged = [w for w in ways if not w.get('tags')]
    tagged = [w for w in ways if w.get('tags')]
    print(f"  Total ways: {len(ways)}   Tagged: {len(tagged)}   Untagged: {len(untagged)}\n")

    key_counts = defaultdict(int)
    key_values = defaultdict(lambda: defaultdict(int))
    for w in tagged:
        for k, v in w['tags'].items():
            key_counts[k] += 1
            key_values[k][v] += 1

    for key, count in sorted(key_counts.items(), key=lambda x: -x[1]):
        top_vals = sorted(key_values[key].items(), key=lambda x: -x[1])[:6]
        vals_str = '  '.join(f"{v}({c})" for v, c in top_vals)
        print(f"  {BOLD}{key:<30}{RESET}  {count:>4} ways    {DIM}{vals_str}{RESET}")


def summarise_nodes(nodes):
    header("NODE tag keys — all keys ranked by frequency")

    tagged_nodes = [n for n in nodes if n.get('tags')]
    print(f"  Total nodes: {len(nodes):,}   Tagged (POIs): {len(tagged_nodes):,}\n")

    key_counts = defaultdict(int)
    key_values = defaultdict(lambda: defaultdict(int))
    for n in tagged_nodes:
        for k, v in n['tags'].items():
            key_counts[k] += 1
            key_values[k][v] += 1

    for key, count in sorted(key_counts.items(), key=lambda x: -x[1]):
        top_vals = sorted(key_values[key].items(), key=lambda x: -x[1])[:6]
        vals_str = '  '.join(f"{v}({c})" for v, c in top_vals)
        print(f"  {BOLD}{key:<30}{RESET}  {count:>4} nodes   {DIM}{vals_str}{RESET}")


def show_named_features(elements):
    header("Named features (name tag present — potential waypoints / story anchors)")

    named = [e for e in elements if e.get('tags', {}).get('name')]
    named.sort(key=lambda e: e['type'])

    print(f"  Total named elements: {len(named)}\n")

    by_type = defaultdict(list)
    for e in named:
        by_type[e['type']].append(e)

    for etype, items in sorted(by_type.items()):
        subheader(f"{etype}s  ({len(items)})")
        for e in items[:30]:
            tags = e.get('tags', {})
            name = tags.get('name', '')
            category = next((f"{k}={v}" for k, v in tags.items() if k not in ('name', 'source', 'created_by')), 'unclassified')
            lat = e.get('lat', '')
            lon = e.get('lon', '')
            coords = f"({lat:.4f}, {lon:.4f})" if lat and lon else "(way — no direct coords)"
            print(f"    {GREEN}{name:<45}{RESET}  {DIM}{category:<35} {coords}{RESET}")
        if len(items) > 30:
            print(f"    {DIM}... and {len(items)-30} more{RESET}")


def show_natural_features(elements):
    header("Natural / ecological features (natural=* tag)")

    natural_els = [e for e in elements if 'natural' in e.get('tags', {})]
    print(f"  Total elements with natural=*: {len(natural_els)}\n")

    by_value = defaultdict(list)
    for e in natural_els:
        val = e['tags']['natural']
        by_value[val].append(e)

    for val, items in sorted(by_value.items(), key=lambda x: -len(x[1])):
        named_count = sum(1 for e in items if e.get('tags', {}).get('name'))
        print(f"  {BOLD}natural={val:<20}{RESET}  {len(items):>4} elements  "
              f"{DIM}({named_count} named){RESET}")
        named = [e for e in items if e.get('tags', {}).get('name')]
        for e in named[:5]:
            print(f"    {DIM}→ {e['tags'].get('name', '')}{RESET}")


def show_leisure_features(elements):
    header("Leisure / amenity features (parks, pitches, gardens)")

    leisure_els = [e for e in elements if 'leisure' in e.get('tags', {})]
    amenity_els = [e for e in elements if 'amenity' in e.get('tags', {})]

    for label, els in [('leisure', leisure_els), ('amenity', amenity_els)]:
        subheader(f"{label}=*  ({len(els)} elements)")
        by_value = defaultdict(list)
        for e in els:
            by_value[e['tags'][label]].append(e)
        for val, items in sorted(by_value.items(), key=lambda x: -len(x[1])):
            named = [e.get('tags', {}).get('name', '') for e in items if e.get('tags', {}).get('name')]
            names_preview = ', '.join(named[:3])
            print(f"    {val:<35}  {len(items):>3}   {DIM}{names_preview}{RESET}")


def show_raw_examples(elements):
    header("RAW DATA EXAMPLES — how OSM primitives look in the API response")

    node_index = {e['id']: e for e in elements if e['type'] == 'node'}
    ways = [e for e in elements if e['type'] == 'way']
    relations = [e for e in elements if e['type'] == 'relation']

    # ── Example 1: a bare node (part of a way, no tags) ──────────
    subheader("1. Bare node  (coordinate point, no meaning of its own)")
    bare_nodes = [n for n in elements if n['type'] == 'node' and not n.get('tags')]
    if bare_nodes:
        n = bare_nodes[0]
        print(f"""
  {{
    "type": "node",
    "id": {n['id']},
    "lat": {n['lat']},
    "lon": {n['lon']}
    // no tags — this node exists only to give coordinates to a way that references it
  }}
""")

    # ── Example 2: a tagged node (a real POI) ────────────────────
    subheader("2. Tagged node  (a POI — has meaning on its own)")
    tagged_nodes = [n for n in elements if n['type'] == 'node' and n.get('tags')]
    named_nodes = [n for n in tagged_nodes if n.get('tags', {}).get('name')]
    example_node = named_nodes[0] if named_nodes else (tagged_nodes[0] if tagged_nodes else None)
    if example_node:
        tags = example_node['tags']
        tags_fmt = json.dumps(tags, indent=4, ensure_ascii=False)
        tags_indented = '\n'.join('    ' + line for line in tags_fmt.splitlines())
        print(f"""
  {{
    "type": "node",
    "id": {example_node['id']},
    "lat": {example_node['lat']},
    "lon": {example_node['lon']},
    "tags": {tags_indented}
    // tags give this node its meaning — type, name, etc.
  }}
""")

    # ── Example 3: a named way with node IDs → resolved coords ───
    subheader("3. Way  (ordered list of node IDs → assembled into a line or polygon)")
    named_ways = [w for w in ways if w.get('tags', {}).get('name') and len(w.get('nodes', [])) >= 3]
    example_way = named_ways[0] if named_ways else (ways[0] if ways else None)
    if example_way:
        tags = example_way.get('tags', {})
        node_ids = example_way.get('nodes', [])
        print(f"""
  RAW from API:
  {{
    "type": "way",
    "id": {example_way['id']},
    "tags": {json.dumps(tags, ensure_ascii=False)},
    "nodes": {node_ids[:6]} {'...' if len(node_ids) > 6 else ''}   // {len(node_ids)} node IDs total
  }}

  To get coordinates, look up each node ID in the node list:
""")
        print(f"  {'NODE ID':<15}  {'LAT':>10}  {'LON':>11}  {'(position in way)':>20}")
        print(f"  {'-'*60}")
        for pos, nid in enumerate(node_ids[:8]):
            n = node_index.get(nid)
            if n:
                is_first = "(start)" if pos == 0 else ""
                is_closed = "(= start → closed polygon)" if pos > 0 and nid == node_ids[0] else ""
                note = is_first or is_closed
                print(f"  {nid:<15}  {n['lat']:>10.6f}  {n['lon']:>11.6f}  {note}")
        if len(node_ids) > 8:
            print(f"  ... {len(node_ids) - 8} more nodes")
        is_closed = node_ids[0] == node_ids[-1] if len(node_ids) > 1 else False
        shape = "closed polygon (area)" if is_closed else "open line (path/road)"
        print(f"\n  First node ID == last node ID: {is_closed}  →  this is a {shape}")

    # ── Example 4: a relation ────────────────────────────────────
    subheader("4. Relation  (named group of ways and/or nodes)")
    if relations:
        r = relations[0]
        tags = r.get('tags', {})
        members = r.get('members', [])
        print(f"""
  {{
    "type": "relation",
    "id": {r['id']},
    "tags": {json.dumps(tags, ensure_ascii=False)},
    "members": [   // {len(members)} members total
""")
        for m in members[:6]:
            mtype = m.get('type', '?')
            mid = m.get('ref', '?')
            role = m.get('role', '') or '(no role)'
            print(f"      {{ type: {mtype!r}, ref: {mid}, role: {role!r} }},")
        if len(members) > 6:
            print(f"      ... {len(members) - 6} more members")
        print("""    ]
    // each member is a node/way/relation ID + an optional role (e.g. "outer", "inner", "stop")
    // roles let you describe a park with holes, a route with stops, etc.
  }
""")
        member_types = defaultdict(int)
        for m in members:
            member_types[m.get('type', '?')] += 1
        print(f"  Member breakdown: {dict(member_types)}")
    else:
        print(f"  {DIM}No relations returned in this bbox{RESET}")


def export_sample_json(elements, out_path='prototypes/osm_sample.json'):
    header(f"Exporting annotated sample JSON → {out_path}")

    tagged = [e for e in elements if e.get('tags')]
    sample = tagged[:200]

    with open(out_path, 'w') as f:
        json.dump(sample, f, indent=2)
    print(f"  Written {len(sample)} tagged elements to {out_path}")
    print(f"  {DIM}(first 200 tagged elements — open to inspect raw tag structure){RESET}")


def main():
    global OSM_BBOX
    bbox = sys.argv[1] if len(sys.argv) > 1 else OSM_BBOX
    label = sys.argv[2] if len(sys.argv) > 2 else bbox
    OSM_BBOX = bbox

    print(f"\n{BOLD}OSM Data Explorer — {label}{RESET}")
    print(f"{DIM}bbox: {bbox}{RESET}")

    elements = fetch_osm()
    print(f"\n  {BOLD}Total elements returned: {len(elements):,}{RESET}")

    by_type = summarise_element_types(elements)
    nodes = by_type.get('node', [])
    ways = by_type.get('way', [])
    relations = by_type.get('relation', [])

    show_raw_examples(elements)
    summarise_ways(ways)
    summarise_nodes(nodes)
    show_named_features(elements)
    show_natural_features(elements)
    show_leisure_features(elements)
    safe_label = label.replace(' ', '_').replace('/', '-')
    export_sample_json(elements, out_path=f'prototypes/osm_sample_{safe_label}.json')

    header("SUMMARY")
    print(f"  Nodes:     {len(nodes):>6,}")
    print(f"  Ways:      {len(ways):>6,}")
    print(f"  Relations: {len(relations):>6,}")
    tagged_nodes = [n for n in nodes if n.get('tags')]
    named = [e for e in elements if e.get('tags', {}).get('name')]
    natural_els = [e for e in elements if 'natural' in e.get('tags', {})]
    print(f"\n  Tagged nodes (POIs): {len(tagged_nodes):>5,}")
    print(f"  Named elements:      {len(named):>5,}")
    print(f"  Natural features:    {len(natural_els):>5,}")
    print(f"\n  {GREEN}Raw sample exported to prototypes/osm_sample.json{RESET}\n")


if __name__ == '__main__':
    main()
