#!/usr/bin/env python3
"""
PROTOTYPE — latency_provider_comparison.py
Question: production's taxon-resolution LLM call (google/gemini-3.7-flash,
unpinned "Balanced" routing) has a P90 latency of ~4s even on its best
provider. Pinning specific low-P90 model+provider combinations found on
OpenRouter's own latency pages (?percentile=p90#providers) - does that
actually bring this step down toward ~1s, and does accuracy against the
existing eval suite hold up?

Standalone (per this directory's convention) - TAXON_GUIDANCE /
QUERY_SCHEMA_TOOL_OPENAI / SEEDED_QUERIES / parsing logic are duplicated
from app/backend/services/prompts/taxon_resolution.py and
app/backend/tests/evals/test_taxon_resolution_eval.py (current production
text, not model_comparison_spike.py's older pre-ADR-017 copy - that one
still used the stale "Testudines is class-rank" rule). SEEDED_QUERIES here
is a verbatim copy of every case in that eval file, including the turtles/
reptiles fix confirmed live against production's real model this session
(Testudines must be order-rank; the reptiles 4-way expansion lists it
last, matching the prompt's own enumeration order, not alphabetical/
original order).

Candidates and their pinned provider come directly from the model+provider
combinations named in this session's conversation (each model's OpenRouter
page, P90 percentile view, provider column) - not chosen by this script.
Provider pinning uses OpenRouter's `provider.order` + `allow_fallbacks:
false` so each call actually goes to the named provider, not whichever
fast backend the model's own `:nitro` routing variant picks today (nitro
already biases toward low-latency providers, but can rotate; pinning tests
the exact combination observed). Provider names are the human-readable
strings as OpenRouter's UI shows them - if a pin is rejected (wrong slug),
that candidate's calls show up as HTTP-error rows, not a crash.

Run standalone (prints full per-query log, then a latency summary table):
  source venv/bin/activate && python prototypes/scripts/latency_provider_comparison.py \
    2>&1 | tee prototypes/logs/latency_provider_comparison_$(date +%Y%m%d_%H%M%S).log
Or via the browser UI: prototypes/scripts/server_latency_provider_comparison.py
"""

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import httpx
from dotenv import load_dotenv

load_dotenv()

# --- Copied from model_comparison_spike.py (itself copied from app/backend) ---

TAXON_GUIDANCE = """You turn a nature-walk request into a list of GBIF taxon
filters by calling produce_gbif_taxonomy_query.

- taxonRank must be one of: kingdom, phylum, class, order, family, genus.
- taxonValue is the scientific or common name at that rank (e.g. "Aves" for birds).
- Never use unranked clade names (e.g. "Vertebrata", "Tetrapoda") — they
  don't resolve reliably in GBIF's taxonomy.
- taxonFilters is a list, so it can hold more than one entry. If the
  request names multiple distinct groups (e.g. "birds and insects" or
  "a mix of birds, plants and mammals"), return one entry per group.
- Some lay terms don't map to a single GBIF rank and must be expanded into
  several entries covering the real groups involved:
  - "reptiles" -> four entries: class-rank Crocodylia, Squamata,
    Sphenodontia, plus order-rank Testudines (see the Testudines rule below).
  - "fish" -> seven entries covering the highest-volume fish groups:
    order-rank Perciformes, Cypriniformes, Scorpaeniformes, Gadiformes,
    Clupeiformes, Salmoniformes, plus class-rank Elasmobranchii (sharks
    and rays) — most ray-finned fish have no single GBIF class.
- Whenever "Testudines" is the taxonValue — whether the request says
  "turtles", "tortoises", "terrapins", or is part of a broader "reptiles"
  request — taxonRank MUST always be "order", never "class".
- If the request has no clear taxonomic signal, return an empty list.
  Do not guess.
"""

QUERY_SCHEMA_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "produce_gbif_taxonomy_query",
        "description": (
            "Translate the user's natural-language nature-walk request into a "
            "list of GBIF taxon filters, empty if there's no clear taxonomic signal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "taxonFilters": {
                    "type": "array",
                    "description": (
                        "One entry per distinct taxon group named or implied by "
                        "the request. Each entry is a scientific rank + name "
                        "pair, never a numeric key. Empty if there's no clear "
                        "taxonomic signal."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "taxonRank": {
                                "type": "string",
                                "enum": ["kingdom", "phylum", "class", "order", "family", "genus"],
                            },
                            "taxonValue": {"type": "string"},
                        },
                        "required": ["taxonRank", "taxonValue"],
                    },
                }
            },
            "required": ["taxonFilters"],
        },
    },
}

SEEDED_QUERIES = [
    ("I want to see birds", [{"taxonRank": "class", "taxonValue": "Aves"}]),
    ("I want to see Plants", [{"taxonRank": "kingdom", "taxonValue": "Plantae"}]),
    ("I want to see Insects", [{"taxonRank": "class", "taxonValue": "Insecta"}]),
    ("I want to see Fungi", [{"taxonRank": "kingdom", "taxonValue": "Fungi"}]),
    ("I want to see Turtles", [{"taxonRank": "order", "taxonValue": "Testudines"}]),
    ("I'm not interested in animals", []),
    ("Solve pi and 4+4 for me", []),
    ("show me interesting things", []),
    (
        "show me plants and birds",
        [
            {"taxonRank": "kingdom", "taxonValue": "Plantae"},
            {"taxonRank": "class", "taxonValue": "Aves"},
        ],
    ),
    (
        "show me birds, plants and insects",
        [
            {"taxonRank": "class", "taxonValue": "Aves"},
            {"taxonRank": "kingdom", "taxonValue": "Plantae"},
            {"taxonRank": "class", "taxonValue": "Insecta"},
        ],
    ),
    (
        "show me some fish",
        [
            {"taxonRank": "order", "taxonValue": "Perciformes"},
            {"taxonRank": "order", "taxonValue": "Cypriniformes"},
            {"taxonRank": "order", "taxonValue": "Scorpaeniformes"},
            {"taxonRank": "order", "taxonValue": "Gadiformes"},
            {"taxonRank": "order", "taxonValue": "Clupeiformes"},
            {"taxonRank": "order", "taxonValue": "Salmoniformes"},
            {"taxonRank": "class", "taxonValue": "Elasmobranchii"},
        ],
    ),
    (
        "I want to see reptiles",
        [
            {"taxonRank": "class", "taxonValue": "Crocodylia"},
            {"taxonRank": "class", "taxonValue": "Squamata"},
            {"taxonRank": "class", "taxonValue": "Sphenodontia"},
            {"taxonRank": "order", "taxonValue": "Testudines"},
        ],
    ),
    ("I'm not interested in fish but I like birds", [{"taxonRank": "class", "taxonValue": "Aves"}]),
    (
        "not interested in insects but I like plants and birds",
        [
            {"taxonRank": "kingdom", "taxonValue": "Plantae"},
            {"taxonRank": "class", "taxonValue": "Aves"},
        ],
    ),
    ("no fish please", []),
    ("I want to see beetles", [{"taxonRank": "order", "taxonValue": "Coleoptera"}]),
    ("I want to see dragonflies", [{"taxonRank": "order", "taxonValue": "Odonata"}]),
    ("I want to see oak trees", [{"taxonRank": "genus", "taxonValue": "Quercus"}]),
    ("I want to see mammals", [{"taxonRank": "class", "taxonValue": "Mammalia"}]),
    ("I want to see European robins", [{"taxonRank": "genus", "taxonValue": "Erithacus"}]),
    (
        "show me fungi and dragonflies",
        [
            {"taxonRank": "kingdom", "taxonValue": "Fungi"},
            {"taxonRank": "order", "taxonValue": "Odonata"},
        ],
    ),
    (
        "show me dragonflies and oak trees",
        [
            {"taxonRank": "order", "taxonValue": "Odonata"},
            {"taxonRank": "genus", "taxonValue": "Quercus"},
        ],
    ),
    (
        "show me fungi and oak trees",
        [
            {"taxonRank": "kingdom", "taxonValue": "Fungi"},
            {"taxonRank": "genus", "taxonValue": "Quercus"},
        ],
    ),
    (
        "show me fungi, dragonflies and oak trees",
        [
            {"taxonRank": "kingdom", "taxonValue": "Fungi"},
            {"taxonRank": "order", "taxonValue": "Odonata"},
            {"taxonRank": "genus", "taxonValue": "Quercus"},
        ],
    ),
    (
        "show me fungi, dragonflies, oak trees, birds and plants",
        [
            {"taxonRank": "kingdom", "taxonValue": "Fungi"},
            {"taxonRank": "order", "taxonValue": "Odonata"},
            {"taxonRank": "genus", "taxonValue": "Quercus"},
            {"taxonRank": "class", "taxonValue": "Aves"},
            {"taxonRank": "kingdom", "taxonValue": "Plantae"},
        ],
    ),
]

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 60.0
WALL_CLOCK_TIMEOUT = 30.0

# "provider": None means unpinned (OpenRouter's default routing) - used for
# the current-prod baseline, so the comparison includes "what we actually
# run today" as a row, not just the candidates.
CANDIDATES = [
    {"model": "google/gemini-3.7-flash", "provider": None, "label": "gemini-3.7-flash (current prod, unpinned)"},
    {"model": "z-ai/glm-5.2-20260616:nitro", "provider": "Baseten", "label": "glm-5.2-20260616:nitro @ Baseten Fast"},
    {"model": "openai/gpt-oss-120b:nitro", "provider": "Cerebras", "label": "gpt-oss-120b:nitro @ Cerebras"},
    {"model": "openai/gpt-oss-120b:nitro", "provider": None, "label": "gpt-oss-120b:nitro (generic, unpinned)"},
    {"model": "openai/gpt-4.1-nano-2025-04-14:nitro", "provider": "OpenAI", "label": "gpt-4.1-nano-2025-04-14:nitro @ OpenAI/Azure"},
    {"model": "deepseek/deepseek-v4.1-flash-20261017:nitro", "provider": "Baseten", "label": "deepseek-v4.1-flash-20261017:nitro @ Baseten"},
    {"model": "deepseek/deepseek-v4.1-flash-20261017:nitro", "provider": None, "label": "deepseek-v4.1-flash-20261017:nitro (generic, unpinned)"},
]

_EXECUTOR = ThreadPoolExecutor(max_workers=8)


# --- Pure parsing/comparison logic (copied from model_comparison_spike.py) ---


def parse_openrouter_tool_call(response: dict) -> tuple[list[dict] | None, str | None]:
    if response.get("error"):
        return None, str(response["error"].get("message") or response["error"])

    choices = response.get("choices") or []
    if not choices:
        return None, "no choices in response"

    message = choices[0].get("message") or {}
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        content = message.get("content")
        return None, f"model did not call the tool; returned prose instead: {content!r}"

    arguments_raw = tool_calls[0].get("function", {}).get("arguments", "")
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        return None, f"tool call arguments were not valid JSON: {exc} (raw={arguments_raw!r})"

    if "taxonFilters" not in arguments:
        return None, f"tool call arguments missing 'taxonFilters' key: {arguments!r}"

    return arguments["taxonFilters"], None


def filters_match(actual: list[dict] | None, expected: list[dict]) -> bool:
    if actual is None:
        return False
    return actual == expected


# --- Model calls -----------------------------------------------------------


def resolve_pinned(query: str, candidate: dict) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": candidate["model"],
        "messages": [
            {"role": "system", "content": TAXON_GUIDANCE},
            {"role": "user", "content": query},
        ],
        "tools": [QUERY_SCHEMA_TOOL_OPENAI],
        "tool_choice": {"type": "function", "function": {"name": "produce_gbif_taxonomy_query"}},
        "usage": {"include": True},
    }
    if candidate["provider"]:
        payload["provider"] = {"order": [candidate["provider"]], "allow_fallbacks": False}

    start = time.perf_counter()
    future = _EXECUTOR.submit(httpx.post, OPENROUTER_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    try:
        response = future.result(timeout=WALL_CLOCK_TIMEOUT)
        raw = response.json()
    except FutureTimeoutError:
        return _result(candidate, query, None, f"wall-clock timeout after {WALL_CLOCK_TIMEOUT}s", time.perf_counter() - start, None)
    except httpx.HTTPError as exc:
        return _result(candidate, query, None, f"request failed: {exc}", time.perf_counter() - start, None)
    except json.JSONDecodeError as exc:
        return _result(candidate, query, None, f"response was not JSON: {exc}", time.perf_counter() - start, None)

    elapsed = time.perf_counter() - start
    cost_usd = (raw.get("usage") or {}).get("cost")
    if response.status_code != 200:
        return _result(candidate, query, None, f"HTTP {response.status_code}: {json.dumps(raw)[:500]}", elapsed, cost_usd)

    taxon_filters, error = parse_openrouter_tool_call(raw)
    return _result(candidate, query, taxon_filters, error, elapsed, cost_usd)


def _result(candidate, query, taxon_filters, error, elapsed_s, cost_usd) -> dict:
    return {
        "label": candidate["label"],
        "model": candidate["model"],
        "provider": candidate["provider"],
        "query": query,
        "taxon_filters": taxon_filters,
        "error": error,
        "elapsed_s": round(elapsed_s, 2),
        "cost_usd": cost_usd,
    }


def run_all_candidates(query: str) -> list[dict]:
    """Sequential, matches model_comparison_spike.py's run_all_models."""
    return run_candidates(query)


def run_candidates(query: str, labels: list[str] | None = None) -> list[dict]:
    """Runs just the candidates named in `labels` (CANDIDATES' "label" field),
    or all of them if labels is None/empty - lets a single model/provider
    pin be tested on its own instead of always paying for all 5 per query."""
    selected = [c for c in CANDIDATES if not labels or c["label"] in labels]
    return [resolve_pinned(query, c) for c in selected]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


# --- Standalone CLI runner (full log of all seeded queries x all candidates) ---


def _print_result(result: dict, expected: list[dict] | None) -> None:
    status = f"{RED}ERROR{RESET}"
    if result["error"] is None:
        status = f"{GREEN}PASS{RESET}" if filters_match(result["taxon_filters"], expected) else f"{RED}FAIL{RESET}"
    cost_str = f"${result['cost_usd']:.6f}" if result["cost_usd"] is not None else "(cost n/a)"
    print(f"  {result['label']:<45} [{result['elapsed_s']:>5.2f}s, {cost_str}]  {status}")
    if result["error"]:
        print(f"    {RED}error: {result['error']}{RESET}")
    elif not filters_match(result["taxon_filters"], expected):
        print(f"    {YELLOW}got: {result['taxon_filters']!r}{RESET}")


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(f"{RED}OPENROUTER_API_KEY is not set.{RESET}")
        return

    print(f"{BOLD}PROTOTYPE: latency_provider_comparison — {len(SEEDED_QUERIES)} eval queries x {len(CANDIDATES)} model/provider pins{RESET}\n")

    pass_counts = {c["label"]: 0 for c in CANDIDATES}
    error_counts = {c["label"]: 0 for c in CANDIDATES}
    latencies: dict[str, list[float]] = {c["label"]: [] for c in CANDIDATES}
    cost_totals = {c["label"]: 0.0 for c in CANDIDATES}

    for query, expected in SEEDED_QUERIES:
        print(f"{BOLD}Query:{RESET} {query!r}  {DIM}expected={expected!r}{RESET}")
        for result in run_all_candidates(query):
            label = result["label"]
            latencies[label].append(result["elapsed_s"])
            if result["error"] is None:
                pass_counts[label] += int(filters_match(result["taxon_filters"], expected))
            else:
                error_counts[label] += 1
            if result["cost_usd"] is not None:
                cost_totals[label] += result["cost_usd"]
            _print_result(result, expected)
        print()

    n = len(SEEDED_QUERIES)
    print(f"{BOLD}Latency + accuracy summary ({n} queries), sorted by P90 latency:{RESET}")
    rows = []
    for c in CANDIDATES:
        label = c["label"]
        vals = latencies[label]
        rows.append((percentile(vals, 0.9), label, statistics.mean(vals), statistics.median(vals), max(vals), pass_counts[label], error_counts[label], cost_totals[label] / n))
    rows.sort(key=lambda r: r[0])
    for p90_s, label, mean_s, median_s, max_s, passed, errors, avg_cost in rows:
        print(
            f"  {label:<45} mean={mean_s:5.2f}s  median={median_s:5.2f}s  "
            f"p90={p90_s:5.2f}s  max={max_s:5.2f}s  pass={passed}/{n}  errors={errors}  avg_cost=${avg_cost:.6f}/query"
        )

    print(
        f"\n{DIM}Note: provider pin strings are the human-readable names from each "
        f"model's OpenRouter page - if a candidate shows only ERROR rows above, "
        f"the pinned provider slug is probably wrong and needs checking against "
        f"OpenRouter's current provider list for that model, not evidence the "
        f"approach doesn't work.{RESET}"
    )


if __name__ == "__main__":
    main()
