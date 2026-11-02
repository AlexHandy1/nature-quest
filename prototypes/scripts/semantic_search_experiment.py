"""
PROTOTYPE — semantic_search_experiment.py (Track A, step 3 of 3)

Questions this answers, against the 100-species index built by
semantic_search_index.py:
  1. Speed — how long does a free-text semantic search take locally?
     Cold start (model load + DB open), then per-query embed vs DuckDB search
     split (in-process, many repeats), then client-observed HTTP round trip
     against a running server_semantic_search.py (if --http is given).
  2. Quality — over a fixed suite of descriptive/thematic queries, does the
     top 5 contain at least one relevant species (hit@5), and what fraction
     of the top 5 is relevant (precision@5)? Relevance is judged
     mechanically from each species' GBIF-derived group or its hand-assigned
     trait tags (semantic_search_corpus.py CANDIDATES), never from the
     embedded text itself.
  3. Calibration signal — top-1 cosine scores for in-domain vs deliberately
     out-of-domain queries, as a first look at the "confidence threshold"
     open question in PLANNING_EMBEDDINGS_FEATURES_291026.md.

Run: venv/bin/python prototypes/scripts/semantic_search_experiment.py [--http http://127.0.0.1:5057] \
       | tee prototypes/logs/semantic_search_experiment_$(date +%Y%m%d_%H%M%S).log
Writes prototypes/artifacts/semantic_search_experiment_results.json
"""

import argparse
import json
import statistics
import time
from pathlib import Path

K = 5
REPEATS = 20
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
RESULTS_PATH = ARTIFACTS / "semantic_search_experiment_results.json"


def groups(*names):
    return {"groups": list(names)}


def tags(*names):
    return {"tags": list(names)}


# (id, query, relevance spec, note). Relevance = species group in `groups`
# OR any of `tags` present. The first three are the brief's named scenarios.
SCENARIOS = [
    ("flies_birds", "something that flies", groups("bird"), "brief: birds in top 5"),
    ("swims_fish", "something that swims", groups("fish"), "brief: fish in top 5"),
    ("camo_wings_butterflies", "something with colourful wings that use camouflage", groups("butterfly"), "brief: butterflies in top 5"),
    ("pollinators", "I want to see pollinators", tags("pollinator"), "brief: headline thematic query"),
    # Paraphrase variants of the brief's scenarios
    ("flying_animals", "flying animals", tags("flies"), "paraphrase"),
    ("aquatic_animals", "aquatic animals", tags("swims", "freshwater", "marine"), "paraphrase"),
    ("camo_butterflies", "camouflaged butterflies", groups("butterfly"), "paraphrase"),
    ("flowers_bees_visit", "insects that visit flowers", tags("pollinator"), "paraphrase"),
    # Further thematic/descriptive queries (the class the LLM resolver can't answer)
    ("pond", "animals that might live in a pond", tags("freshwater"), "thematic"),
    ("night", "creatures that come out at night", tags("nocturnal"), "thematic"),
    ("venomous", "something venomous or poisonous", tags("venomous"), "thematic"),
    ("raptors", "birds of prey", tags("raptor"), "thematic"),
    ("trees", "tall trees", tags("tree"), "thematic"),
    ("endangered", "endangered animals", tags("threatened"), "thematic"),
    ("ocean", "something that lives in the ocean", tags("marine"), "thematic"),
    ("shells", "animals with shells", tags("shell"), "thematic"),
    ("predator", "a big predator", tags("predator"), "thematic"),
    ("eight_legs", "creepy crawlies with eight legs", groups("arachnid"), "thematic"),
    ("slimy", "something slimy", groups("amphibian", "mollusc"), "thematic"),
    ("colourful_birds", "colourful birds", groups("bird"), "thematic"),
    # Lexical controls (taxonomy-shaped; the existing LLM path already handles these)
    ("bees", "bees", groups("bee"), "lexical control"),
    ("frogs", "frogs and toads", groups("amphibian"), "lexical control"),
    ("mushrooms", "mushrooms", groups("fungus"), "lexical control"),
    ("flowers", "flowers", groups("plant"), "lexical control"),
]

OUT_OF_DOMAIN = [
    "quarterly tax return spreadsheet",
    "best pizza in Madrid",
    "how do I reset my router password",
]


# ── Scoring (unit tested in test_semantic_search.py) ───────────────────

def hit_at_k(results: list[dict], is_relevant) -> bool:
    return any(is_relevant(r) for r in results)


def precision_at_k(results: list[dict], is_relevant) -> float:
    return sum(1 for r in results if is_relevant(r)) / len(results) if results else 0.0


def relevance_predicate(spec: dict):
    wanted_groups = set(spec.get("groups", []))
    wanted_tags = set(spec.get("tags", []))
    return lambda r: r["group"] in wanted_groups or bool(wanted_tags & set(r.get("tags") or []))


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def latency_summary(values: list[float]) -> dict:
    return {"n": len(values), "mean_ms": statistics.mean(values), "p50_ms": pct(values, 0.5),
            "p95_ms": pct(values, 0.95), "max_ms": max(values)}


def fmt(s: dict) -> str:
    return f"mean={s['mean_ms']:6.2f} p50={s['p50_ms']:6.2f} p95={s['p95_ms']:6.2f} max={s['max_ms']:6.2f} (n={s['n']})"


def run_http(base_url: str, queries: list[str]) -> dict:
    import requests
    session = requests.Session()
    round_trips, server_totals = [], []
    for _ in range(REPEATS // 4):
        for q in queries:
            t0 = time.perf_counter()
            resp = session.post(f"{base_url}/search", json={"query": q}, timeout=30)
            round_trips.append((time.perf_counter() - t0) * 1000)
            resp.raise_for_status()
            server_totals.append(resp.json()["timings_ms"]["total"])
    return {"client_round_trip": latency_summary(round_trips), "server_total": latency_summary(server_totals)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", help="base URL of a running server_semantic_search.py")
    args = parser.parse_args()

    import duckdb
    from semantic_search_index import DB_PATH, embed_query, load_model, search_store

    # ── Cold start ──
    t0 = time.perf_counter()
    from sentence_transformers import SentenceTransformer  # noqa: F401 — import cost is part of cold start
    import_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    model = load_model()
    load_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    connect_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    first = search_store(con, embed_query(model, "something that flies"), k=K)
    first_query_ms = (time.perf_counter() - t0) * 1000
    assert len(first) == K
    cold = {"import_ms": import_ms, "model_load_ms": load_ms, "db_connect_ms": connect_ms, "first_query_ms": first_query_ms}
    print("Cold start:", {k: round(v, 1) for k, v in cold.items()})

    # ── Warm in-process latency ──
    all_queries = [s[1] for s in SCENARIOS] + OUT_OF_DOMAIN
    embed_times, search_times, total_times = [], [], []
    for _ in range(REPEATS):
        for q in all_queries:
            t0 = time.perf_counter()
            vec = embed_query(model, q)
            t1 = time.perf_counter()
            search_store(con, vec, k=K)
            t2 = time.perf_counter()
            embed_times.append((t1 - t0) * 1000)
            search_times.append((t2 - t1) * 1000)
            total_times.append((t2 - t0) * 1000)
    warm = {"embed": latency_summary(embed_times), "duckdb_search": latency_summary(search_times),
            "total": latency_summary(total_times)}
    print(f"\nWarm in-process latency over {len(total_times)} queries (ms):")
    for stage, s in warm.items():
        print(f"  {stage:14} {fmt(s)}")

    http = None
    if args.http:
        http = run_http(args.http, all_queries)
        print(f"\nHTTP via {args.http} (ms):")
        for stage, s in http.items():
            print(f"  {stage:18} {fmt(s)}")

    # ── Quality ──
    print("\n" + "=" * 100 + "\nQuery scenarios (top 5)\n" + "=" * 100)
    scenario_results = []
    for sid, query, spec, note in SCENARIOS:
        results = search_store(con, embed_query(model, query), k=K)
        is_rel = relevance_predicate(spec)
        hit, prec = hit_at_k(results, is_rel), precision_at_k(results, is_rel)
        print(f"\n[{'PASS' if hit else 'FAIL'}] {sid} — \"{query}\"  ({note}; relevant = {spec})  P@5={prec:.1f}")
        for i, r in enumerate(results, 1):
            print(f"   {i}. {'✓' if is_rel(r) else '·'} {r['score']:.3f}  {r['common_name']} ({r['scientific_name']}) "
                  f"[{r['group']}, {r['tier']}] tags={r['tags']}")
        scenario_results.append({
            "id": sid, "query": query, "relevance": spec, "note": note, "hit_at_5": hit, "precision_at_5": prec,
            "top1_score": results[0]["score"],
            "results": [{k: r[k] for k in ("common_name", "scientific_name", "group", "tier", "tags", "score")}
                        | {"relevant": is_rel(r)} for r in results],
        })

    ood = []
    print("\nOut-of-domain controls:")
    for q in OUT_OF_DOMAIN:
        results = search_store(con, embed_query(model, q), k=K)
        print(f"   \"{q}\": top1={results[0]['score']:.3f} {results[0]['common_name']}; "
              f"top5={[r['common_name'] for r in results]}")
        ood.append({"query": q, "top1_score": results[0]["score"], "top5": [r["common_name"] for r in results]})

    # ── Aggregates ──
    hits = sum(s["hit_at_5"] for s in scenario_results)
    mean_p = statistics.mean(s["precision_at_5"] for s in scenario_results)
    by_note: dict[str, list] = {}
    for s in scenario_results:
        by_note.setdefault(s["note"].split(":")[0], []).append(s)
    print("\n" + "=" * 100)
    print(f"hit@5: {hits}/{len(scenario_results)}   mean P@5: {mean_p:.2f}")
    for note, items in by_note.items():
        print(f"  {note:16} hit@5 {sum(i['hit_at_5'] for i in items)}/{len(items)}  "
              f"mean P@5 {statistics.mean(i['precision_at_5'] for i in items):.2f}")
    in_top1 = [s["top1_score"] for s in scenario_results]
    ood_top1 = [o["top1_score"] for o in ood]
    print(f"top-1 cosine: in-domain min={min(in_top1):.3f} median={statistics.median(in_top1):.3f} max={max(in_top1):.3f}; "
          f"out-of-domain max={max(ood_top1):.3f}")

    corpus_tiers = dict(con.execute("SELECT tier, count(*) FROM species GROUP BY tier").fetchall())
    result_tiers: dict[str, int] = {}
    for s in scenario_results:
        for r in s["results"]:
            result_tiers[r["tier"]] = result_tiers.get(r["tier"], 0) + 1
    print(f"tier mix — corpus: {corpus_tiers}; across all top-5 results: {result_tiers}")

    RESULTS_PATH.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "k": K, "repeats": REPEATS,
        "cold_start_ms": cold, "warm_latency_ms": warm, "http_latency_ms": http,
        "hit_at_5": hits, "scenario_count": len(scenario_results), "mean_precision_at_5": mean_p,
        "scenarios": scenario_results, "out_of_domain": ood,
        "tier_mix": {"corpus": corpus_tiers, "results": result_tiers},
    }, indent=2, default=float))
    print(f"\nWrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
