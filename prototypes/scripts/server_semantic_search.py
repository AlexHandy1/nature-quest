"""
PROTOTYPE — server_semantic_search.py

Thin Flask wrapper over semantic_search_index.py's search path (same import
exception as server.py → e2e_walk_spike_server.py): GET / serves
web/index_semantic_search.html, POST /search {"query": "..."} returns the top
5 species by cosine similarity plus server-side embed/search timings. No LLM,
no map, no polygon — semantic search over the 100-species index only.

Question this answers: does free-text semantic species search feel
responsive and sensible in a browser, before any production routing-cascade
work (PLANNING_EMBEDDINGS_FEATURES_291026.md, Track A)?

Run: venv/bin/python prototypes/scripts/server_semantic_search.py  → http://127.0.0.1:5057
"""

import time
from pathlib import Path

import duckdb
from flask import Flask, jsonify, request, send_file

from semantic_search_index import DB_PATH, embed_query, load_model, search_store

PORT = 5057
MAX_QUERY_LENGTH = 200
K = 5
WEB_PAGE = Path(__file__).resolve().parent.parent / "web" / "index_semantic_search.html"

print("Loading model…")
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
