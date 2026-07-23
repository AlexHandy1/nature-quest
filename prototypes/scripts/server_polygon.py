#!/usr/bin/env python3
"""
PROTOTYPE — server_polygon.py
Fork of server.py (untouched — see this codebase's "copy, don't edit a
validated checkpoint" convention). Only difference: /run-walk accepts a
user-drawn polygon (list of [lat, lon] vertices) instead of the fixed
`location: 'retiro'` string, converts it to GBIF-format WKT + a centroid,
and passes both through to e2e_walk_spike_polygon.run_pipeline().

Question: does a user-drawn polygon survive the trip from browser Leaflet.draw
coordinates -> GBIF WKT geometry -> real occurrence search -> waypoint
ordering from the drawn area's own centre, end-to-end?
Throwaway. Do not promote to production.

Serves prototypes/web/index_polygon.html (a fork of index.html with the
"draw your own area" control enabled) instead of index.html, on a different
port (5051) so both prototypes can be run side by side for comparison.

Requires ANTHROPIC_API_KEY in the environment (same as server.py).

Run: source venv/bin/activate && python prototypes/scripts/server_polygon.py
Then open http://localhost:5051 in a browser.
"""

import os
import sys
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_walk_spike_polygon import run_pipeline, DEFAULT_MODEL  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5051
MIN_POLYGON_POINTS = 3

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index_polygon.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


def polygon_points_to_wkt(points):
    """points is a list of [lat, lon] pairs (Leaflet's order). GBIF WKT wants
    "lon lat" pairs and a closed ring (first vertex repeated as the last)."""
    ring = [(lon, lat) for lat, lon in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    coords = ",".join(f"{lon} {lat}" for lon, lat in ring)
    return f"POLYGON(({coords}))"


def polygon_centroid(points):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


@app.route("/run-walk", methods=["POST"])
def run_walk():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()
    polygon = body.get("polygon")

    print(f"\n{BOLD}[server] /run-walk  polygon_points={len(polygon) if polygon else 0}  query={user_query!r}{RESET}")

    if not user_query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400

    if not polygon or len(polygon) < MIN_POLYGON_POINTS:
        return jsonify({"status": "error", "message": "Draw an area with at least 3 points before searching."}), 400

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"status": "error", "message": "Server misconfigured: ANTHROPIC_API_KEY is not set."}), 500

    polygon_wkt = polygon_points_to_wkt(polygon)
    center_lat, center_lon = polygon_centroid(polygon)
    print(f"  {DIM}geometry={polygon_wkt}{RESET}")
    print(f"  {DIM}centre=({center_lat:.5f}, {center_lon:.5f}){RESET}")

    start = time.perf_counter()
    try:
        result = run_pipeline(
            user_query,
            intent_model=DEFAULT_MODEL,
            description_model=DEFAULT_MODEL,
            narrative_model=DEFAULT_MODEL,
            polygon_wkt=polygon_wkt,
            center_lat=center_lat,
            center_lon=center_lon,
            open_browser=False,
        )
    except Exception as e:
        print(f"{RED}[server] pipeline error: {e}{RESET}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Pipeline failed: {e}"}), 500

    elapsed_s = time.perf_counter() - start

    if result is None:
        return jsonify({"status": "error", "message": "No species found for this query in the drawn area."}), 200

    species_payload = [
        {
            "name": sp["common_name"] or sp["species"],
            "sci": sp["species"],
            "lat": sp["hotspot_lat"],
            "lon": sp["hotspot_lon"],
            "img": sp.get("image_url") or "",
            "desc": sp["description"],
        }
        for sp in result["species"]
    ]

    print(f"{GREEN}[server] /run-walk done in {elapsed_s:.1f}s, {len(species_payload)} species{RESET}")

    return jsonify({
        "status": "ok",
        "query": user_query,
        "species": species_payload,
        "intro": result["intro"],
        "waypoints": result["waypoints"],
    })


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY is not set — export it before running this server.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}PROTOTYPE: nature-walker server — user-drawn polygon{RESET}")
    print(f"{DIM}Serving {WEB_DIR}/index_polygon.html + POST /run-walk (blocking, calls e2e_walk_spike_polygon.run_pipeline){RESET}")
    print(f"\n  {GREEN}Open http://localhost:{PORT} in a browser{RESET}\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
