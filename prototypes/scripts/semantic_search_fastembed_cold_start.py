"""Quick spike: does fastembed/ONNX actually cut cold start vs. the
sentence-transformers baseline in semantic_search_experiment.py, using the
same 100-species corpus and the same "<common> (<scientific>): <extract>"
text recipe? Run as its own fresh process so import time is genuinely cold:

    venv/bin/python prototypes/scripts/semantic_search_fastembed_cold_start.py
"""
import json
import time
from pathlib import Path

CORPUS_PATH = Path(__file__).parent.parent / "artifacts" / "semantic_search_corpus.json"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def build_text(record: dict) -> str:
    return f"{record['common_name']} ({record['scientific_name']}): {record['extract']}"


def main() -> None:
    t0 = time.perf_counter()
    from fastembed import TextEmbedding  # noqa: F401 — import cost is part of cold start
    import_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    model = TextEmbedding(model_name=MODEL_NAME)
    model_load_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    first_vec = next(model.embed(["something that flies"]))
    first_query_ms = (time.perf_counter() - t0) * 1000
    assert len(first_vec) == 384

    cold = {
        "import_ms": import_ms,
        "model_load_ms": model_load_ms,
        "first_query_ms": first_query_ms,
        "total_ready_ms": import_ms + model_load_ms + first_query_ms,
    }
    print("Cold start (fastembed/ONNX):", {k: round(v, 1) for k, v in cold.items()})

    corpus = json.load(open(CORPUS_PATH))["selected"]
    texts = [build_text(r) for r in corpus]

    t0 = time.perf_counter()
    vectors = list(model.embed(texts))
    batch_ms = (time.perf_counter() - t0) * 1000
    print(f"\nBatch-embedded {len(texts)} species in {batch_ms:.1f}ms "
          f"({batch_ms / len(texts):.2f}ms/species)")

    # warm single-query timing, same shape as the sentence-transformers experiment
    warm_times = []
    for _ in range(20):
        t0 = time.perf_counter()
        next(model.embed(["something that flies"]))
        warm_times.append((time.perf_counter() - t0) * 1000)
    warm_times.sort()
    p50 = warm_times[len(warm_times) // 2]
    print(f"Warm single-query embed p50 over {len(warm_times)} reps: {p50:.1f}ms")

    results = {"cold_start_ms": cold, "batch_embed_ms": batch_ms,
               "per_species_ms": batch_ms / len(texts), "warm_p50_ms": p50,
               "corpus_size": len(texts), "model": MODEL_NAME, "dim": len(vectors[0])}
    out_path = Path(__file__).parent.parent / "artifacts" / "semantic_search_fastembed_cold_start_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
