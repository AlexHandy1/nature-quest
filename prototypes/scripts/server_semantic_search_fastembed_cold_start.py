"""
PROTOTYPE — same shape as server_semantic_search.py's own "Ready in Xms"
startup measurement (model load happens before app.run), but swapping
sentence-transformers for fastembed/ONNX, to compare real server cold-start.

Run: venv/bin/python prototypes/scripts/server_semantic_search_fastembed_cold_start.py
"""
import time

t_process_start = time.perf_counter()

from flask import Flask  # noqa: E402 — import cost is part of server cold start
from fastembed import TextEmbedding  # noqa: E402

print("Loading model…")
t0 = time.perf_counter()
MODEL = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
next(MODEL.embed(["warm"]))  # first real inference, same as the baseline's "first query"
ready_ms = (time.perf_counter() - t0) * 1000
total_ms = (time.perf_counter() - t_process_start) * 1000
print(f"Ready in {ready_ms:.0f}ms (model load only), {total_ms:.0f}ms (from process start)")

app = Flask(__name__)
