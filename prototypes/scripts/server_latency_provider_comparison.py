#!/usr/bin/env python3
"""
PROTOTYPE — server_latency_provider_comparison.py
Browser page to compare pinned low-P90-latency OpenRouter model+provider
combinations against production's current unpinned google/gemini-3.7-flash,
across the exact test_taxon_resolution_eval.py eval cases.

Thin HTTP wrapper around latency_provider_comparison.py - no second
implementation, per this directory's exception for server wrappers (same
pattern as server_model_comparison.py).

Run: source venv/bin/activate && python prototypes/scripts/server_latency_provider_comparison.py
Then open http://localhost:5052 in a browser.
"""

import os
import sys

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from latency_provider_comparison import (  # noqa: E402
    CANDIDATES,
    SEEDED_QUERIES,
    filters_match,
    run_candidates,
)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5052

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index_latency_provider_comparison.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/api/seeded-queries")
def seeded_queries():
    return jsonify([{"query": q, "expected": e} for q, e in SEEDED_QUERIES])


@app.route("/api/candidates")
def candidates():
    return jsonify([{"label": c["label"], "model": c["model"], "provider": c["provider"]} for c in CANDIDATES])


@app.route("/api/run", methods=["POST"])
def run():
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    expected = body.get("expected")
    labels = body.get("labels")  # None/empty = all candidates; else just these

    if not query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400

    print(f"\n{BOLD}[server] /api/run  query={query!r}  expected={expected!r}  labels={labels!r}{RESET}")

    results = run_candidates(query, labels)
    for r in results:
        passed = filters_match(r["taxon_filters"], expected) if expected is not None else None
        r["passed"] = passed
        colour = GREEN if passed else (RED if passed is False else DIM)
        cost_str = f"${r['cost_usd']:.6f}" if r["cost_usd"] is not None else "cost n/a"
        print(f"  {colour}{r['label']:<45} [{r['elapsed_s']}s, {cost_str}] taxon_filters={r['taxon_filters']!r} error={r['error']!r}{RESET}")

    return jsonify({"status": "ok", "query": query, "expected": expected, "results": results})


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(f"{RED}OPENROUTER_API_KEY is not set — export it before running this server.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}PROTOTYPE: latency/provider comparison server{RESET}")
    print(f"{DIM}Serving {WEB_DIR} + POST /api/run ({len(CANDIDATES)} pinned model/provider candidates, sequential){RESET}")
    print(f"\n  {GREEN}Open http://localhost:{PORT} in a browser{RESET}\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
