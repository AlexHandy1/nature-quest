#!/usr/bin/env python3
"""
PROTOTYPE — server.py
Question: can a browser-driven landing page trigger the e2e_walk_spike
pipeline and render its result as a live single-page quest-log experience,
instead of the pipeline only being runnable from the CLI?
Throwaway. Do not promote to production.

Minimal Flask server — the first thing in this codebase that serves HTTP.
Two responsibilities only:
  1. Serve the single-page frontend (prototypes/web/index.html and its
     static assets).
  2. POST /run-walk — a single blocking endpoint that calls
     e2e_walk_spike_server.run_pipeline() in-process (open_browser=False)
     and returns the result as JSON for the frontend to render itself.

Deliberately synchronous/blocking (no background job + polling) for this
round — the whole pipeline is one call, ~15-25s. Background-job polling for
real per-step progress ("Generating query...", "Finding species...", etc)
is a known next step for the fuller version, not built here (see
WORK_SUMMARY for this session).

Imports run_pipeline() from e2e_walk_spike_server.py (a copy of the working
e2e_walk_spike.py CLI spike, kept separate so the validated CLI script is
never edited in place — see this session's WORK_SUMMARY). This is the first
cross-script import in this codebase; justified because this is a thin HTTP
wrapper around the pipeline, not a second implementation of it.

Requires ANTHROPIC_API_KEY in the environment (same as e2e_walk_spike.py).

Run: source venv/bin/activate && python prototypes/scripts/server.py
Then open http://localhost:5050 in a browser.
"""

import os
import sys
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_walk_spike_server import run_pipeline, DEFAULT_MODEL  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5050

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/run-walk", methods=["POST"])
def run_walk():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()
    location = body.get("location", "retiro")

    print(f"\n{BOLD}[server] /run-walk  location={location}  query={user_query!r}{RESET}")

    if not user_query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400

    if location != "retiro":
        # Only Retiro is wired up in this prototype round — see the
        # disabled "draw your own area" control in the frontend.
        return jsonify({"status": "error", "message": f"Location '{location}' is not supported yet."}), 400

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"status": "error", "message": "Server misconfigured: ANTHROPIC_API_KEY is not set."}), 500

    start = time.perf_counter()
    try:
        result = run_pipeline(
            user_query,
            intent_model=DEFAULT_MODEL,
            description_model=DEFAULT_MODEL,
            narrative_model=DEFAULT_MODEL,
            open_browser=False,
        )
    except Exception as e:
        print(f"{RED}[server] pipeline error: {e}{RESET}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Pipeline failed: {e}"}), 500

    elapsed_s = time.perf_counter() - start

    if result is None:
        return jsonify({"status": "error", "message": "No species found for this query near Retiro Park."}), 200

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

    print(f"\n{BOLD}PROTOTYPE: nature-walker server{RESET}")
    print(f"{DIM}Serving {WEB_DIR} + POST /run-walk (blocking, calls e2e_walk_spike_server.run_pipeline){RESET}")
    print(f"\n  {GREEN}Open http://localhost:{PORT} in a browser{RESET}\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
