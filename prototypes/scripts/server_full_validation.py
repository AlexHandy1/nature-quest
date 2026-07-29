#!/usr/bin/env python3
"""
PROTOTYPE — server_full_validation.py
Fork of server_polygon.py (untouched — see this codebase's "copy, don't edit
a validated checkpoint" convention). Splits the single blocking /run-walk
call into two endpoints, matching e2e_walk_spike_full_validation.py's
resolve_species_query() / run_pipeline() split:

  POST /gbif-species-query — runs STEPS 1-5 only (cheap: one LLM call + free
    GBIF calls). Returns a validation verdict (status/message/species/notes)
    BEFORE any of the expensive STEP 6-8 work (descriptions, narrative, map
    render) happens.
  POST /run-walk — takes an already-resolved species list (from a prior
    /gbif-species-query call) and runs STEPS 6-8 only.

Question: does splitting the pipeline at this seam let the frontend show a
"needs_clarification" prompt (cases 1/4 — no taxonomic signal, or a resolved
filter that found nothing here) and pause for an explicit user decision,
without ever silently falling back to an unfiltered most_observed search?
Throwaway. Do not promote to production.

Species data is round-tripped through the client between the two calls
(stateless design — see this session's design conversation for why: no
server-side session state exists anywhere else in this codebase yet, and
introducing it here would answer a question this prototype isn't asking).

Serves prototypes/web/index_full_validation.html on port 5052 (separate from
server.py's 5050 and server_polygon.py's 5051, so all three can run side by
side for comparison).

Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/server_full_validation.py
Then open http://localhost:5052 in a browser.
"""

import os
import sys
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_walk_spike_full_validation import (  # noqa: E402
    resolve_species_query, run_pipeline, DEFAULT_MODEL, DEFAULT_GRID_N,
)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5052
MIN_POLYGON_POINTS = 3

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index_full_validation.html")


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


def species_to_client(sp):
    return {
        "sci": sp["species"],
        "species_key": sp.get("species_key"),
        "count": sp["count"],
        "kingdom": sp["kingdom"],
        "lat": sp["hotspot_lat"],
        "lon": sp["hotspot_lon"],
        "points": sp.get("occurrence_points", []),
    }


def species_from_client(sp):
    return {
        "species": sp["sci"],
        "species_key": sp.get("species_key"),
        "count": sp["count"],
        "kingdom": sp["kingdom"],
        "hotspot_lat": sp["lat"],
        "hotspot_lon": sp["lon"],
        "occurrence_points": sp.get("points", []),
    }


@app.route("/gbif-species-query", methods=["POST"])
def gbif_species_query():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()
    polygon = body.get("polygon")
    override = bool(body.get("override"))

    print(f"\n{BOLD}[server] /gbif-species-query  polygon_points={len(polygon) if polygon else 0}  "
          f"query={user_query!r}  override={override}{RESET}")

    if not user_query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400
    if not polygon or len(polygon) < MIN_POLYGON_POINTS:
        return jsonify({"status": "error", "message": "Draw an area with at least 3 points before searching."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"status": "error", "message": "Server misconfigured: ANTHROPIC_API_KEY is not set."}), 500

    polygon_wkt = polygon_points_to_wkt(polygon)
    center_lat, center_lon = polygon_centroid(polygon)

    start = time.perf_counter()
    try:
        result = resolve_species_query(
            user_query, polygon_wkt, center_lat, center_lon,
            grid_n=DEFAULT_GRID_N, intent_model=DEFAULT_MODEL, override=override,
        )
    except Exception as e:
        print(f"{RED}[server] resolve_species_query error: {e}{RESET}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Query resolution failed: {e}"}), 500

    elapsed_s = time.perf_counter() - start
    print(f"{GREEN}[server] /gbif-species-query done in {elapsed_s:.1f}s -> status={result['status']} "
          f"species={len(result['species'])} notes={len(result['notes'])}{RESET}")

    return jsonify({
        "status": result["status"],
        "message": result["message"],
        "species": [species_to_client(sp) for sp in result["species"]],
        "notes": result["notes"],
    })


@app.route("/run-walk", methods=["POST"])
def run_walk():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()
    species = body.get("species")
    notes = body.get("notes") or []

    print(f"\n{BOLD}[server] /run-walk  species={len(species) if species else 0}  query={user_query!r}{RESET}")

    if not user_query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400
    if not species:
        return jsonify({"status": "error", "message": "No resolved species to build a walk from."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"status": "error", "message": "Server misconfigured: ANTHROPIC_API_KEY is not set."}), 500

    species_internal = [species_from_client(sp) for sp in species]

    start = time.perf_counter()
    try:
        result = run_pipeline(species_internal, user_query, notes=notes, open_browser=False)
    except Exception as e:
        print(f"{RED}[server] run_pipeline error: {e}{RESET}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Pipeline failed: {e}"}), 500

    elapsed_s = time.perf_counter() - start

    species_payload = [
        {
            "name": sp["common_name"] or sp["species"],
            "sci": sp["species"],
            "lat": sp["hotspot_lat"],
            "lon": sp["hotspot_lon"],
            "img": sp.get("image_url") or "",
            "desc": sp["description"],
            "points": sp.get("occurrence_points", []),
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
        "notes": notes,
    })


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY is not set — export it before running this server.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}PROTOTYPE: nature-walker server — full query validation{RESET}")
    print(f"{DIM}Serving {WEB_DIR}/index_full_validation.html + POST /gbif-species-query (STEPS 1-5) "
          f"+ POST /run-walk (STEPS 6-8){RESET}")
    print(f"\n  {GREEN}Open http://localhost:{PORT} in a browser{RESET}\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
