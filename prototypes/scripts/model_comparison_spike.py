#!/usr/bin/env python3
"""
PROTOTYPE — model_comparison_spike.py
Question: do cheaper OpenRouter models match production Haiku's
forced-tool-call taxon resolution
(app/backend/services/anthropic_client.py::resolve_taxon_filters) well
enough, and cheaply enough, to be worth building real model flexibility
into prod?

Copies (does not import) TAXON_GUIDANCE/QUERY_SCHEMA_TOOL from
services/anthropic_client.py and the seeded queries + expected filters
from app/backend/tests/evals/test_taxon_resolution_eval.py, per this
directory's "copy, don't import from app/" convention — this prototype must
not touch or depend on production code, and must not touch GBIF at all.

Haiku is deliberately excluded from the comparison here — it's the known
baseline that already passes prod's eval, so it isn't a useful column in a
tool that's specifically about finding a cheaper alternative that also
clears that bar. Candidates go through OpenRouter's OpenAI-compatible chat
completions REST endpoint via plain httpx (matches services/tts.py's
existing OpenRouter pattern in this codebase — no OpenAI SDK). All calls
run sequentially, at each provider's default temperature (unset), full raw
output captured for every call including failures — this is a comparison
tool, not a pass/fail gate. Each call requests OpenRouter's `usage.cost`
accounting (`usage: {include: true}`) so cost-per-query is the real
provider-reported dollar amount, not an estimate from a hardcoded price
table.

Run standalone (prints full log for all seeded queries x all candidates):
  source venv/bin/activate && python prototypes/scripts/model_comparison_spike.py
Or via the browser UI: prototypes/scripts/server_model_comparison.py
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import httpx
from dotenv import load_dotenv

load_dotenv()

# --- Copied from app/backend/services/anthropic_client.py ---------------

TAXON_GUIDANCE = """You turn a nature-walk request into a list of GBIF taxon
filters by calling produce_gbif_query.

- taxonRank must be one of: kingdom, phylum, class, order, family, genus.
- taxonValue is the scientific or common name at that rank (e.g. "Aves" for birds).
- Never use unranked clade names (e.g. "Vertebrata", "Tetrapoda") — they
  don't resolve reliably in GBIF's taxonomy.
- taxonFilters is a list, so it can hold more than one entry. If the
  request names multiple distinct groups (e.g. "birds and insects" or
  "a mix of birds, plants and mammals"), return one entry per group.
- Some lay terms don't map to a single GBIF rank and must be expanded into
  several entries covering the real groups involved:
  - "reptiles" -> four class-rank entries: Crocodylia, Squamata, Testudines,
    Sphenodontia (there is no single "Reptilia" class in GBIF's backbone).
  - "fish" -> seven entries covering the highest-volume fish groups:
    order-rank Perciformes, Cypriniformes, Scorpaeniformes, Gadiformes,
    Clupeiformes, Salmoniformes, plus class-rank Elasmobranchii (sharks
    and rays) — most ray-finned fish have no single GBIF class.
- If the request has no clear taxonomic signal, return an empty list.
  Do not guess.
"""

QUERY_SCHEMA_TOOL_ANTHROPIC = {
    "name": "produce_gbif_query",
    "description": (
        "Translate the user's natural-language nature-walk request into a "
        "list of GBIF taxon filters, empty if there's no clear taxonomic signal."
    ),
    "input_schema": {
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
}

# Same schema, OpenAI/OpenRouter tool-calling shape (function wrapper).
QUERY_SCHEMA_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": QUERY_SCHEMA_TOOL_ANTHROPIC["name"],
        "description": QUERY_SCHEMA_TOOL_ANTHROPIC["description"],
        "parameters": QUERY_SCHEMA_TOOL_ANTHROPIC["input_schema"],
    },
}

# --- OpenRouter candidates ------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# httpx's own `timeout` bounds the gap *between* reads, not total wall-clock
# time — a response that keeps trickling data (e.g. keep-alive pings while a
# model is still generating) never hits it, no matter how long the call
# actually takes overall (confirmed live: google/gemini-2.5-flash-lite ran
# 183.8s on an ambiguous species query, well past httpx's own 60s timeout).
# WALL_CLOCK_TIMEOUT is a real deadline enforced in a worker thread on top
# of that, so one stalling model can't hold up the whole sequential run.
REQUEST_TIMEOUT = 60.0
WALL_CLOCK_TIMEOUT = 30.0

OPENROUTER_MODELS = [
    "google/gemini-3.7-flash",
    "z-ai/glm-5.3-flash",
    "google/gemini-2.5-flash-lite",
    "deepseek/deepseek-v4-flash",
]

ALL_MODELS = OPENROUTER_MODELS

# --- Seeded queries, copied from app/backend/tests/evals/test_taxon_resolution_eval.py ---

SEEDED_QUERIES = [
    ("I want to see birds", [{"taxonRank": "class", "taxonValue": "Aves"}]),
    ("I want to see Plants", [{"taxonRank": "kingdom", "taxonValue": "Plantae"}]),
    ("I want to see Insects", [{"taxonRank": "class", "taxonValue": "Insecta"}]),
    ("I want to see Fungi", [{"taxonRank": "kingdom", "taxonValue": "Fungi"}]),
    ("I want to see Turtles", [{"taxonRank": "class", "taxonValue": "Testudines"}]),
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
            {"taxonRank": "class", "taxonValue": "Testudines"},
            {"taxonRank": "class", "taxonValue": "Sphenodontia"},
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


# --- Pure parsing/comparison logic ---------------------------------------


def parse_openrouter_tool_call(response: dict) -> tuple[list[dict] | None, str | None]:
    """Extract taxonFilters from an OpenRouter chat-completions response
    (OpenAI tool-calling shape). Returns (taxon_filters, error) — exactly
    one of the two is non-None. Never raises: a malformed/unexpected
    response is a data point for this comparison, not a crash."""
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
    """Order-sensitive, exact match — same standard as
    test_taxon_resolution_eval.py's `taxon_filters == [...]` assertions."""
    if actual is None:
        return False
    return actual == expected


# --- Model calls ----------------------------------------------------------

# Shared executor so a stalling call's worker thread can be abandoned (its
# socket is never forcibly closed — Python can't cancel a blocking network
# call — but the caller stops waiting on it) without leaking a new thread
# per query.
_EXECUTOR = ThreadPoolExecutor(max_workers=8)


def resolve_via_openrouter(query: str, model: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": TAXON_GUIDANCE},
            {"role": "user", "content": query},
        ],
        "tools": [QUERY_SCHEMA_TOOL_OPENAI],
        "tool_choice": {"type": "function", "function": {"name": "produce_gbif_query"}},
        # Asks OpenRouter to report real provider-billed cost on the
        # response, so cost-per-query is measured, not estimated from a
        # hand-maintained price table.
        "usage": {"include": True},
    }

    start = time.perf_counter()
    future = _EXECUTOR.submit(httpx.post, OPENROUTER_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    try:
        response = future.result(timeout=WALL_CLOCK_TIMEOUT)
        raw = response.json()
    except FutureTimeoutError:
        return _result(
            model, query, None,
            f"wall-clock timeout after {WALL_CLOCK_TIMEOUT}s (model may still be generating server-side; "
            f"httpx's own {REQUEST_TIMEOUT}s timeout only bounds gaps between reads, not total response time)",
            None, time.perf_counter() - start, None,
        )
    except httpx.HTTPError as exc:
        return _result(model, query, None, f"request failed: {exc}", None, time.perf_counter() - start, None)
    except json.JSONDecodeError as exc:
        return _result(model, query, None, f"response was not JSON: {exc} (raw={response.text[:500]!r})", None, time.perf_counter() - start, None)

    elapsed = time.perf_counter() - start
    cost_usd = (raw.get("usage") or {}).get("cost")
    if response.status_code != 200:
        return _result(model, query, None, f"HTTP {response.status_code}: {json.dumps(raw)[:500]}", raw, elapsed, cost_usd)

    taxon_filters, error = parse_openrouter_tool_call(raw)
    return _result(model, query, taxon_filters, error, raw, elapsed, cost_usd)


def _result(model, query, taxon_filters, error, raw, elapsed_s, cost_usd) -> dict:
    return {
        "model": model,
        "query": query,
        "taxon_filters": taxon_filters,
        "error": error,
        "raw": raw,
        "elapsed_s": round(elapsed_s, 2),
        "cost_usd": cost_usd,
    }


def run_all_models(query: str) -> list[dict]:
    """Sequential, matches the existing evals' execution style."""
    return [resolve_via_openrouter(query, model) for model in OPENROUTER_MODELS]


# --- Standalone CLI runner (full log of all seeded queries x all models) ---


def _print_result(result: dict, expected: list[dict] | None) -> None:
    status = f"{RED}ERROR{RESET}"
    if result["error"] is None:
        if expected is not None:
            status = f"{GREEN}PASS{RESET}" if filters_match(result["taxon_filters"], expected) else f"{RED}FAIL{RESET}"
        else:
            status = f"{DIM}(no expected value){RESET}"

    cost_str = f"${result['cost_usd']:.6f}" if result["cost_usd"] is not None else "(cost n/a)"
    print(f"  {BOLD}{result['model']}{RESET}  [{result['elapsed_s']}s, {cost_str}]  {status}")
    print(f"    taxon_filters: {result['taxon_filters']!r}")
    if result["error"]:
        print(f"    {RED}error: {result['error']}{RESET}")


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print(f"{RED}OPENROUTER_API_KEY is not set.{RESET}")
        return

    print(f"{BOLD}PROTOTYPE: model_comparison_spike — {len(SEEDED_QUERIES)} queries x {len(ALL_MODELS)} models{RESET}\n")

    pass_counts = {m: 0 for m in ALL_MODELS}
    cost_totals = {m: 0.0 for m in ALL_MODELS}
    for query, expected in SEEDED_QUERIES:
        print(f"{BOLD}Query:{RESET} {query!r}  {DIM}expected={expected!r}{RESET}")
        for result in run_all_models(query):
            _print_result(result, expected)
            if result["error"] is None and filters_match(result["taxon_filters"], expected):
                pass_counts[result["model"]] += 1
            if result["cost_usd"] is not None:
                cost_totals[result["model"]] += result["cost_usd"]
        print()

    print(f"{BOLD}Summary ({len(SEEDED_QUERIES)} queries):{RESET}")
    for model in ALL_MODELS:
        avg_cost = cost_totals[model] / len(SEEDED_QUERIES)
        print(f"  {model}: {pass_counts[model]}/{len(SEEDED_QUERIES)} passed, avg ${avg_cost:.6f}/query, total ${cost_totals[model]:.6f}")


if __name__ == "__main__":
    main()
