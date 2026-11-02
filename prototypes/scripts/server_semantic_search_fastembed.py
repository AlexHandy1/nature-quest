"""
PROTOTYPE — server_semantic_search_fastembed.py

Same Flask wrapper and same web page as server_semantic_search.py, but over
the fastembed/ONNX-built index (semantic_search_index_fastembed.py). Runs on
a different port so both backends can be compared side by side in a browser.

Run: venv/bin/python prototypes/scripts/semantic_search_index_fastembed.py   (build the index once)
     venv/bin/python prototypes/scripts/server_semantic_search_fastembed.py  → http://127.0.0.1:5058
"""

import time
from pathlib import Path

import duckdb
from flask import Flask, jsonify, request, send_file

from semantic_search_index import search_store
from semantic_search_index_fastembed import DB_PATH, embed_query, load_model

PORT = 5058
MAX_QUERY_LENGTH = 200
K = 5
WEB_PAGE = Path(__file__).resolve().parent.parent / "web" / "index_semantic_search.html"

print("Loading model (fastembed/ONNX)…")
t0 = time.perf_counter()
MODEL = load_model()
CON = duckdb.connect(str(DB_PATH), read_only=True)
print(f"Ready in {(time.perf_counter() - t0) * 1000:.0f}ms")

app = Flask(__name__)


@app.get("/")
def index():
    return send_file(WEB_PAGE)


@app.post("/search")
def search():
    query = ((request.get_json(silent=True) or {}).get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "message": "Type something to search for."}), 400
    if len(query) > MAX_QUERY_LENGTH:
        return jsonify({"status": "error", "message": f"Keep it under {MAX_QUERY_LENGTH} characters."}), 400

    t0 = time.perf_counter()
    vector = embed_query(MODEL, query)
    t1 = time.perf_counter()
    results = search_store(CON.cursor(), vector, k=K)
    t2 = time.perf_counter()
    timings = {"embed": (t1 - t0) * 1000, "search": (t2 - t1) * 1000, "total": (t2 - t0) * 1000}
    print(f"[search] \"{query}\" embed={timings['embed']:.1f}ms search={timings['search']:.1f}ms → "
          + ", ".join(f"{r['common_name']} ({r['score']:.3f})" for r in results))
    return jsonify({"status": "ok", "query": query, "results": results, "timings_ms": timings})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=True)
