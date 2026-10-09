#!/usr/bin/env python3
"""
PROTOTYPE — server_duckdb_e2e.py
Serves prototypes/web/index_duckdb_e2e.html on port 5053 (separate from
server.py 5050, server_polygon.py 5051, server_full_validation.py 5052, so
all can run side by side). Single POST /query endpoint wraps
duckdb_e2e_prototype.run_query() — fixed Retiro Park area only, no draw
tool: this prototype's question is whether the DuckDB-backed pipeline
itself works end-to-end, not the area-selection UI (already covered by
server_polygon.py/index_polygon.html).

Requires ANTHROPIC_API_KEY in the environment, and the cached Parquet
files prototypes/scratch/*.parquet (run duckdb_parquet_prototype.py
--export first if missing).

Run: source venv/bin/activate && python prototypes/scripts/server_duckdb_e2e.py
Then open http://localhost:5053 in a browser.
"""

import os
import sys
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from duckdb_e2e_prototype import FACT_PARQUET, run_query  # noqa: E402

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
PORT = 5053

BOLD = "\x1b[1m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index_duckdb_e2e.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


@app.route("/query", methods=["POST"])
def query():
    body = request.get_json(silent=True) or {}
    user_query = (body.get("query") or "").strip()

    print(f"\n{BOLD}[server] /query  query={user_query!r}{RESET}")

    if not user_query:
        return jsonify({"status": "error", "message": "Query is empty."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"status": "error", "message": "Server misconfigured: ANTHROPIC_API_KEY is not set."}), 500

    start = time.perf_counter()
    try:
        result = run_query(user_query)
    except Exception as e:
        print(f"{RED}[server] run_query error: {e}{RESET}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Query failed: {e}"}), 500
    elapsed_s = time.perf_counter() - start
    timings = result.get("timings_ms", {})

    print(
        f"{GREEN}[server] /query done in {elapsed_s * 1000:.0f}ms (server-measured) -> "
        f"status={result['status']} species={len(result['species'])} "
        f"[llm={timings.get('llm', 0):.0f}ms duckdb={timings.get('duckdb_total', 0):.0f}ms "
        f"pipeline={timings.get('pipeline_total', 0):.0f}ms]{RESET}"
    )

    return jsonify(result)


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY is not set — export it before running this server.{RESET}")
        sys.exit(1)
    if not os.path.exists(FACT_PARQUET):
        print(f"{RED}{FACT_PARQUET} not found — run duckdb_parquet_prototype.py --export first.{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}PROTOTYPE: DuckDB end-to-end query server{RESET}")
    print(f"  Open http://localhost:{PORT} in a browser\n")
    app.run(port=PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
