#!/usr/bin/env python3
"""
PROTOTYPE — server_model_comparison.py
Question: can a browser page make it fast to compare cheaper OpenRouter
models on taxon resolution — pass/fail against production's ground truth,
and real cost per query — across the seeded eval queries plus arbitrary
free-text ones?

Thin HTTP wrapper around model_comparison_spike.py — no second
implementation, per this directory's exception for server wrappers.

Run: source venv/bin/activate && python prototypes/scripts/server_model_comparison.py
Then open http://localhost:5051 in a browser.
"""

import os
import sys

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_comparison_spike import SEEDED_QUERIES, filters_match, run_all_models  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5051

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index_model_comparison.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/api/seeded-queries")
def seeded_queries():
    return jsonify([{"query": q, "expected": e} for q, e in SEEDED_QUERIES])


@app.route("/api/run", methods=["POST"])
def run():
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    expected = body.get("expected")  # None for free-text queries with no ground truth

    if not query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400

    print(f"\n{BOLD}[server] /api/run  query={query!r}  expected={expected!r}{RESET}")

    results = run_all_models(query)
    for r in results:
        passed = filters_match(r["taxon_filters"], expected) if expected is not None else None
        r["passed"] = passed
        colour = GREEN if passed else (RED if passed is False else DIM)
        cost_str = f"${r['cost_usd']:.6f}" if r["cost_usd"] is not None else "cost n/a"
        print(f"  {colour}{r['model']:<28} [{r['elapsed_s']}s, {cost_str}] taxon_filters={r['taxon_filters']!r} error={r['error']!r}{RESET}")

    return jsonify({"status": "ok", "query": query, "expected": expected, "results": results})


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(f"{RED}OPENROUTER_API_KEY is not set — export it before running this server.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}PROTOTYPE: model comparison server{RESET}")
    print(f"{DIM}Serving {WEB_DIR} + POST /api/run (4 OpenRouter candidates, sequential){RESET}")
    print(f"\n  {GREEN}Open http://localhost:{PORT} in a browser{RESET}\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
