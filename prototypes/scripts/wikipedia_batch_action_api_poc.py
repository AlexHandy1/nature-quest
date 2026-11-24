"""
PROTOTYPE — wikipedia_batch_action_api_poc.py

Question this answers: wikipedia_batch_rate_limit_poc.py confirmed the REST
page/summary endpoint sustains Wikimedia's documented 200 req/min ceiling
cleanly (1,000/1,000 OK), extrapolating to ~20h for the full ~240k-species
batch — a long single run. A live test of the MediaWiki Action API
(action=query&prop=extracts) found it accepts up to 20 titles per request
(exlimit=max, confirmed via `limits.extracts: 20` in the response) and
resolves redirects server-side with redirects=1 — e.g. "Blue tit" ->
"Eurasian blue tit" in one round trip. Since Wikimedia's rate-limit policy
is documented per-request, not per-article, batching 20 species per call
could cut the full-batch request count ~20x (240k species / 20 ≈ 12,000
requests ≈ ~1h at 200/min, instead of ~20h for 240k individual REST calls).

This script tests that at a real, messy scale: the same 1,000-species
stratified sample used by wikipedia_batch_rate_limit_poc.py (same seed, for
direct comparability), batched into groups of 20, paced at the documented
200 req/min (each *request* now carries 20 species, so throughput should
reach well above 200 species/min while staying within the request-rate
policy). Checks: any 429s at this low request volume, correct
redirect/normalization resolution across a real/messy name sample, and
whether responses carrying 20 full extracts at once behave normally
(latency, payload size, missing-page handling).

Run: venv/bin/python prototypes/scripts/wikipedia_batch_action_api_poc.py \
       | tee prototypes/logs/wikipedia_batch_action_api_poc_$(date +%Y%m%d_%H%M%S).log
"""

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import duckdb
import httpx
from dotenv import load_dotenv

load_dotenv()

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
MANIFEST_PATH = ARTIFACTS / "wikipedia_coverage_match_manifest.parquet"
OUTPUT_PATH = ARTIFACTS / "wikipedia_batch_action_api_poc_results.json"

ACTION_API_URL = "https://en.wikipedia.org/w/api.php"
_CONTACT = os.environ.get("WIKIPEDIA_CONTACT_EMAIL")
USER_AGENT = (f"nature-quest-prototype/0.1 ({_CONTACT})" if _CONTACT
              else "nature-quest-prototype/0.1 (contact set via WIKIPEDIA_CONTACT_EMAIL env var)")
REQUEST_TIMEOUT = 30.0

SAMPLE_SIZE = 1000
BATCH_SIZE = 20  # confirmed server max for prop=extracts (limits.extracts in the response)
CONCURRENCY = 3
TARGET_RATE_PER_MIN = 200  # Wikimedia's documented ceiling; each request now covers 20 species
MAX_RETRIES = 1


def load_sample(n: int) -> list[dict]:
    """Same stratified-by-decile sample, same seed, as
    wikipedia_batch_rate_limit_poc.py — directly comparable species set."""
    con = duckdb.connect()
    rows = con.execute(
        f"""
        WITH ranked AS (
            SELECT specieskey, species, common_name, total_occurrence, matched_title,
                   NTILE(10) OVER (ORDER BY total_occurrence) AS decile
            FROM read_parquet(?)
        )
        SELECT specieskey, species, common_name, total_occurrence, matched_title
        FROM ranked
        USING SAMPLE ({n} ROWS) REPEATABLE (42)
        """,
        [str(MANIFEST_PATH)],
    ).fetchall()
    cols = ["specieskey", "species", "common_name", "total_occurrence", "matched_title"]
    return [dict(zip(cols, row)) for row in rows]


def chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class RateLimiter:
    def __init__(self, rate_per_min: int):
        self.min_interval = 60.0 / rate_per_min
        self._lock = asyncio.Lock()
        self._next_slot = time.perf_counter()

    async def wait(self) -> None:
        async with self._lock:
            now = time.perf_counter()
            start = max(now, self._next_slot)
            self._next_slot = start + self.min_interval
            delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)


def resolve_batch(data: dict, requested_titles: list[str]) -> dict[str, dict]:
    """Maps each originally-requested title to its final extract/status,
    following the normalized -> redirects -> pages chain the Action API
    returns. A title can be renamed twice (normalized then redirected)
    before landing on a pages entry."""
    query = data.get("query", {})
    pages_by_title = {p.get("title"): p for p in query.get("pages", {}).values()}

    current = {t: t for t in requested_titles}  # requested title -> current title
    for n in query.get("normalized", []):
        for orig, cur in list(current.items()):
            if cur == n["from"]:
                current[orig] = n["to"]
    for r in query.get("redirects", []):
        for orig, cur in list(current.items()):
            if cur == r["from"]:
                current[orig] = r["to"]

    resolved = {}
    for orig_title, final_title in current.items():
        page = pages_by_title.get(final_title)
        if page is None:
            resolved[orig_title] = {"status": "not_found", "extract": None}
        elif "missing" in page:
            resolved[orig_title] = {"status": "missing", "extract": None}
        elif not page.get("extract"):
            resolved[orig_title] = {"status": "empty_extract", "extract": None}
        else:
            resolved[orig_title] = {"status": "ok", "extract": page["extract"],
                                     "final_title": final_title}
    return resolved


async def fetch_batch(client: httpx.AsyncClient, limiter: RateLimiter,
                       sem: asyncio.Semaphore, records: list[dict]) -> dict:
    titles = [r["matched_title"] for r in records]
    result = {"batch_size": len(records), "attempts": 0, "status": None,
              "latency_ms": None, "error": None, "per_species": {}}
    params = {
        "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
        "exlimit": "max", "redirects": "1", "format": "json",
        "titles": "|".join(titles),
    }

    for attempt in range(1, MAX_RETRIES + 2):
        result["attempts"] = attempt
        await limiter.wait()
        async with sem:
            t0 = time.perf_counter()
            try:
                response = await client.get(ACTION_API_URL, params=params,
                                             timeout=REQUEST_TIMEOUT,
                                             headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                result["latency_ms"] = (time.perf_counter() - t0) * 1000
                result["error"] = f"{type(exc).__name__}: {exc}"
                continue
            result["latency_ms"] = (time.perf_counter() - t0) * 1000
            result["status"] = response.status_code

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if attempt <= MAX_RETRIES:
                await asyncio.sleep(float(retry_after) if retry_after else 5.0)
                continue
            result["error"] = "429 after retry"
            return result

        if response.status_code != 200:
            if attempt <= MAX_RETRIES:
                continue
            result["error"] = f"HTTP {response.status_code}"
            return result

        data = response.json()
        result["per_species"] = resolve_batch(data, titles)
        result["error"] = None
        return result

    return result


async def main() -> None:
    sample = load_sample(SAMPLE_SIZE)
    batches = chunk(sample, BATCH_SIZE)
    print(f"Sampled {len(sample)} species -> {len(batches)} batches of up to {BATCH_SIZE}\n", flush=True)
    print(f"Concurrency={CONCURRENCY}, target rate={TARGET_RATE_PER_MIN} requests/min "
          f"(~{TARGET_RATE_PER_MIN * BATCH_SIZE}/min species-equivalent), "
          f"max_retries={MAX_RETRIES}\n", flush=True)

    limiter = RateLimiter(TARGET_RATE_PER_MIN)
    sem = asyncio.Semaphore(CONCURRENCY)

    run_start = time.perf_counter()
    ok_species = 0
    fail_species = 0
    throttled_batches = 0
    all_species_results: dict[str, dict] = {}

    async with httpx.AsyncClient() as client:
        tasks = [fetch_batch(client, limiter, sem, b) for b in batches]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            res = await coro
            if res["status"] == 429:
                throttled_batches += 1
            batch_ok = sum(1 for v in res["per_species"].values() if v["status"] == "ok")
            batch_fail = res["batch_size"] - batch_ok
            ok_species += batch_ok
            fail_species += batch_fail
            all_species_results.update(res["per_species"])
            print(f"  [batch {i:3}/{len(batches)}] size={res['batch_size']:2} "
                  f"status={res['status']} latency={res['latency_ms']:.0f}ms "
                  f"ok={batch_ok}/{res['batch_size']} err={res['error']} "
                  f"(total ok={ok_species} fail={fail_species})", flush=True)
    wall_s = time.perf_counter() - run_start

    print("\n" + "=" * 70)
    print(f"Wall clock: {wall_s:.1f}s for {len(batches)} batches / {len(sample)} species "
          f"({len(sample)/wall_s:.1f} species/s effective, {len(batches)/wall_s*60:.1f} requests/min actual)")
    print(f"Species OK: {ok_species}/{len(sample)}   Failed/missing: {fail_species}   "
          f"Throttled batches (429): {throttled_batches}/{len(batches)}")

    status_breakdown: dict[str, int] = {}
    for v in all_species_results.values():
        status_breakdown[v["status"]] = status_breakdown.get(v["status"], 0) + 1
    print(f"\nPer-species status breakdown: {status_breakdown}")

    not_ok = [(t, v) for t, v in all_species_results.items() if v["status"] != "ok"]
    if not_ok:
        print(f"\nSample of up to 10 non-ok species:")
        for t, v in not_ok[:10]:
            print(f"  {t!r}: {v['status']}")

    full_batch_estimate_hours = (239_880 / (len(sample) / wall_s)) / 3600
    print(f"\nExtrapolated full-batch (239,880 species) time at this observed rate: "
          f"{full_batch_estimate_hours:.2f}h")
    print("=" * 70)

    OUTPUT_PATH.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample_size": len(sample),
        "batch_size": BATCH_SIZE,
        "num_batches": len(batches),
        "concurrency": CONCURRENCY,
        "target_rate_per_min": TARGET_RATE_PER_MIN,
        "wall_clock_s": wall_s,
        "ok_species": ok_species,
        "fail_species": fail_species,
        "throttled_batches": throttled_batches,
        "status_breakdown": status_breakdown,
        "full_batch_estimate_hours": full_batch_estimate_hours,
        "per_species": all_species_results,
    }, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
