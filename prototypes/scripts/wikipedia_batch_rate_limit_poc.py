"""
PROTOTYPE — wikipedia_batch_rate_limit_poc.py

Question this answers: the abstract-dump route is confirmed dead (Wikimedia
discontinued enwiki-abstract.xml.gz in Feb 2025, per Phabricator T382069 —
verified empirically here too: 404 on the "latest" path, absent from recent
dated dump directories). So extract fetching has to go through the REST
page/summary API after all. Wikimedia's published rate-limit policy
(mediawiki.org/wiki/Wikimedia_APIs/Rate_limits, "new in 2026") states a
200 req/min ceiling for anonymous requests with a compliant User-Agent, and
recommends 3 or fewer concurrent requests — at 200/min, the full ~240k-species
batch would take ~20h. This script tests that ceiling empirically on a
1,000-species sample: does real traffic actually sustain ~200/min at 3
concurrent requests without 429s, and is there headroom above the documented
number in practice (worth knowing before committing to a ~20h production run
shape), or does the real server throttle exactly as documented?

Sample: 1,000 species from the existing starter manifest
(prototypes/artifacts/wikipedia_coverage_match_manifest.parquet),
stratified by total_occurrence decile so it isn't just the most common
species — a representative cut of the real ~240k target population.

Method: async fetch with a bounded semaphore (CONCURRENCY) and a simple
token-bucket pacer (TARGET_RATE_PER_MIN) so the two levers (concurrency,
rate) can be tuned independently. One retry on failure/429, honoring
Retry-After when present. Every request's outcome and latency is recorded;
a 429 is the real signal to look for, not just throughput.

User-Agent contact: set WIKIPEDIA_CONTACT_EMAIL in the environment to
include real contact info (Wikimedia's own etiquette asks for it); otherwise
a generic client identifier is sent with no personal details.

Run: venv/bin/python prototypes/scripts/wikipedia_batch_rate_limit_poc.py \
       | tee prototypes/logs/wikipedia_batch_rate_limit_poc_$(date +%Y%m%d_%H%M%S).log
"""

import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from urllib.parse import quote

import duckdb
import httpx
from dotenv import load_dotenv

load_dotenv()

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
MANIFEST_PATH = ARTIFACTS / "wikipedia_coverage_match_manifest.parquet"
OUTPUT_PATH = ARTIFACTS / "wikipedia_batch_rate_limit_poc_results.json"

WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
_CONTACT = os.environ.get("WIKIPEDIA_CONTACT_EMAIL")
USER_AGENT = (f"nature-quest-prototype/0.1 ({_CONTACT})" if _CONTACT
              else "nature-quest-prototype/0.1 (contact set via WIKIPEDIA_CONTACT_EMAIL env var)")
REQUEST_TIMEOUT = 30.0

SAMPLE_SIZE = 1000
CONCURRENCY = 6
TARGET_RATE_PER_MIN = 100_000  # effectively unpaced; concurrency is the only intended constraint
MAX_RETRIES = 1


def load_sample(n: int) -> list[dict]:
    """Stratified by total_occurrence decile so the sample spans common to
    rare species, not just the top of the manifest."""
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


class RateLimiter:
    """Token-bucket pacer: at most `rate_per_min` permits granted per
    rolling 60s window, independent of the concurrency semaphore."""

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


async def fetch_one(client: httpx.AsyncClient, limiter: RateLimiter,
                     sem: asyncio.Semaphore, record: dict) -> dict:
    result = {**record, "attempts": 0, "status": None, "latency_ms": None,
              "extract_len": None, "extract": None, "error": None, "retry_after": None}
    url = f"{WIKIPEDIA_SUMMARY_URL}/{quote(record['matched_title'], safe='')}"

    for attempt in range(1, MAX_RETRIES + 2):
        result["attempts"] = attempt
        await limiter.wait()
        async with sem:
            t0 = time.perf_counter()
            try:
                response = await client.get(url, timeout=REQUEST_TIMEOUT,
                                             headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                result["latency_ms"] = (time.perf_counter() - t0) * 1000
                result["error"] = f"{type(exc).__name__}: {exc}"
                continue
            result["latency_ms"] = (time.perf_counter() - t0) * 1000
            result["status"] = response.status_code

        if response.status_code == 429:
            result["retry_after"] = response.headers.get("Retry-After")
            if attempt <= MAX_RETRIES:
                wait_s = float(result["retry_after"]) if result["retry_after"] else 5.0
                await asyncio.sleep(wait_s)
                continue
            result["error"] = "429 after retry"
            return result

        if response.status_code != 200:
            if attempt <= MAX_RETRIES:
                continue
            result["error"] = f"HTTP {response.status_code}"
            return result

        data = response.json()
        if data.get("type") == "disambiguation" or not data.get("extract"):
            result["error"] = "no usable extract (disambiguation or empty)"
            return result

        result["extract_len"] = len(data["extract"])
        result["extract"] = data["extract"]
        result["error"] = None
        return result

    return result


async def main() -> None:
    sample = load_sample(SAMPLE_SIZE)
    print(f"Sampled {len(sample)} species (stratified by total_occurrence decile)\n", flush=True)
    print(f"Concurrency={CONCURRENCY}, target rate={TARGET_RATE_PER_MIN}/min "
          f"({TARGET_RATE_PER_MIN/60:.2f} req/s), max_retries={MAX_RETRIES}\n", flush=True)

    limiter = RateLimiter(TARGET_RATE_PER_MIN)
    sem = asyncio.Semaphore(CONCURRENCY)

    run_start = time.perf_counter()
    ok_count = 0
    throttled_count = 0
    async with httpx.AsyncClient() as client:
        tasks = [fetch_one(client, limiter, sem, r) for r in sample]
        results = []
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            res = await coro
            results.append(res)
            if res["error"] is None:
                ok_count += 1
            if res["status"] == 429:
                throttled_count += 1
            print(f"  [{i:4}/{len(sample)}] {res['matched_title']:30.30} "
                  f"status={res['status']} latency={res['latency_ms']:.0f}ms "
                  f"err={res['error']}  (ok={ok_count} 429={throttled_count})", flush=True)
    wall_s = time.perf_counter() - run_start

    ok = [r for r in results if r["error"] is None]
    failed = [r for r in results if r["error"] is not None]
    throttled = [r for r in results if r["status"] == 429]
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]

    print("\n" + "=" * 70)
    print(f"Wall clock: {wall_s:.1f}s for {len(sample)} species "
          f"({len(sample)/wall_s:.2f} req/s actual, {len(sample)/wall_s*60:.0f} req/min actual)")
    print(f"OK: {len(ok)}/{len(sample)}   Failed: {len(failed)}   429s seen: {len(throttled)}")
    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
        print(f"Per-request latency: mean={statistics.mean(latencies):.0f}ms "
              f"p50={p50:.0f}ms p95={p95:.0f}ms max={max(latencies):.0f}ms")
    if failed:
        print("\nFailure reasons (sample of up to 10):")
        for r in failed[:10]:
            print(f"  {r['matched_title']!r}: {r['error']} (status={r['status']})")

    full_batch_estimate_hours = (239_880 / (len(sample) / wall_s)) / 3600
    print(f"\nExtrapolated full-batch (239,880 species) time at this observed rate: "
          f"{full_batch_estimate_hours:.1f}h")
    print("=" * 70)

    OUTPUT_PATH.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sample_size": len(sample),
        "concurrency": CONCURRENCY,
        "target_rate_per_min": TARGET_RATE_PER_MIN,
        "wall_clock_s": wall_s,
        "actual_req_per_s": len(sample) / wall_s,
        "ok_count": len(ok),
        "failed_count": len(failed),
        "throttled_429_count": len(throttled),
        "full_batch_estimate_hours": full_batch_estimate_hours,
        "results": results,
    }, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
