"""
PROTOTYPE — semantic_search_ablation.py (Track A, follow-up to the experiment)

Question this answers: semantic_search_experiment.py's one brief failure was
"something that flies" ranking Spanish Fly / Fly Agaric / Housefly above every
bird — a lexical "fly" match on species *names*. Does the embedded-text recipe
cause that? Compares three recipes over the same 100 species and the same
SCENARIOS, all in memory (numpy dot product — vectors are normalised, so this
equals the DuckDB array_cosine_similarity ranking):
  name+extract  — the shipped recipe: "<common> (<scientific>): <extract>"
  extract_only  — Wikipedia extract alone
  name_only     — "<common> (<scientific>)" alone (lower bound: no description)

Run: venv/bin/python prototypes/scripts/semantic_search_ablation.py | tee prototypes/logs/semantic_search_ablation_$(date +%Y%m%d_%H%M%S).log
Writes prototypes/artifacts/semantic_search_ablation_results.json
"""

import json
import statistics

import numpy as np

from semantic_search_experiment import (K, SCENARIOS, hit_at_k, precision_at_k, relevance_predicate)
from semantic_search_index import ARTIFACTS, CORPUS_PATH, build_embedding_text, load_model

RECIPES = {
    "name+extract": build_embedding_text,
    "extract_only": lambda r: r["extract"],
    "name_only": lambda r: f"{r['common_name']} ({r['scientific_name']})",
}


def main() -> None:
    records = json.loads(CORPUS_PATH.read_text())["selected"]
    model = load_model()
    query_vecs = model.encode([s[1] for s in SCENARIOS], normalize_embeddings=True)

    summary, detail = {}, {}
    for recipe, to_text in RECIPES.items():
        doc_vecs = model.encode([to_text(r) for r in records], batch_size=32, normalize_embeddings=True)
        scores = query_vecs @ doc_vecs.T
        rows = []
        for (sid, query, spec, _note), row_scores in zip(SCENARIOS, scores):
            top = [records[i] for i in np.argsort(-row_scores)[:K]]
            is_rel = relevance_predicate(spec)
            rows.append({"id": sid, "hit_at_5": hit_at_k(top, is_rel), "precision_at_5": precision_at_k(top, is_rel),
                         "top5": [r["common_name"] for r in top]})
        summary[recipe] = {"hit_at_5": sum(r["hit_at_5"] for r in rows),
                           "mean_precision_at_5": statistics.mean(r["precision_at_5"] for r in rows)}
        detail[recipe] = rows

    print(f"{'scenario':24}" + "".join(f"{name:>16}" for name in RECIPES))
    for i, (sid, *_rest) in enumerate(SCENARIOS):
        cells = [f"{'✓' if detail[n][i]['hit_at_5'] else '✗'} P@5={detail[n][i]['precision_at_5']:.1f}" for n in RECIPES]
        print(f"{sid:24}" + "".join(f"{c:>16}" for c in cells))
    print()
    for name, s in summary.items():
        print(f"{name:14} hit@5 {s['hit_at_5']}/{len(SCENARIOS)}  mean P@5 {s['mean_precision_at_5']:.2f}")
    for name in RECIPES:
        flies = next(r for r in detail[name] if r["id"] == "flies_birds")
        print(f"'something that flies' [{name}]: {flies['top5']}")

    (ARTIFACTS / "semantic_search_ablation_results.json").write_text(
        json.dumps({"summary": summary, "detail": detail}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
