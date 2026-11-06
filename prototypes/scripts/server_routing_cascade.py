"""
PROTOTYPE — server_routing_cascade.py

Thin Flask wrapper over routing_cascade.py's resolve_species_for_query (same
thin-wrapper exception as server.py -> e2e_walk_spike_server.py, and
server_semantic_search.py -> semantic_search_index.py): GET / serves
web/index_routing_cascade.html, POST /query {"query": "..."} runs the full
LLM-first / semantic-fallback cascade against a FIXED polygon (Retiro Park —
same polygon as app/backend/tests/test_query.py, per this project's "fixed
test data" convention) and returns which branch fired, the resulting
species, and the per-stage latency breakdown.

Question this answers: does the proposed routing cascade
(PLANNING_EMBEDDINGS_FEATURES_291026.md Track A, LLM-first variant decided
this session) work end-to-end against real production pieces, and is the
combined latency acceptable, in a real browser?

Run: venv/bin/python prototypes/scripts/server_routing_cascade.py → http://127.0.0.1:5059
Needs OPENROUTER_API_KEY in .env (real LLM call, no mocking).
"""

import time
from pathlib import Path

import duckdb
from flask import Flask, jsonify, request, send_file

from routing_cascade import (
    EMBED_DB_PATH,
    build_llm_client,
    resolve_species_for_query,
)
from semantic_search_index_fastembed import embed_query, load_model

PORT = 5059
MAX_QUERY_LENGTH = 200
RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)
WEB_PAGE = Path(__file__).resolve().parent.parent / "web" / "index_routing_cascade.html"

print("Loading LLM client, embedding model, and DuckDB connections…")
t0 = time.perf_counter()
LLM_CLIENT = build_llm_client()
EMBED_MODEL = load_model()
EMBED_CON = duckdb.connect(str(EMBED_DB_PATH), read_only=True)
FACT_CON = duckdb.connect()
print(f"Ready in {(time.perf_counter() - t0) * 1000:.0f}ms")

app = Flask(__name__)


@app.get("/")
def index():
    return send_file(WEB_PAGE)


@app.post("/query")
def query():
    text = ((request.get_json(silent=True) or {}).get("query") or "").strip()
    if not text:
        return jsonify({"status": "error", "message": "Type something to search for."}), 400
    if len(text) > MAX_QUERY_LENGTH:
        return jsonify({"status": "error", "message": f"Keep it under {MAX_QUERY_LENGTH} characters."}), 400

    result = resolve_species_for_query(
        text,
        RETIRO_POLYGON,
        llm_client=LLM_CLIENT,
        embed_model=EMBED_MODEL,
        embed_query_fn=embed_query,
        fact_con=FACT_CON.cursor(),
        embed_con=EMBED_CON.cursor(),
    )
    print(
        f"[{result['branch']}] \"{text}\" total={result['timings_ms']['total']:.0f}ms → "
        + ", ".join(
            s.get("common_name") or s.get("species") or "?" for s in result["species"]
        )
    )
    return jsonify({"status": "ok", "query": text, **result})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, threaded=False)
