"""Production fetch step of the Wikipedia-embeddings pipeline (fetch -> embed
-> build table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md).

Batches up to 20 species per request via the MediaWiki Action API
(action=query&prop=extracts&exintro=1&explaintext=1&exlimit=max&redirects=1),
paced at Wikimedia's documented 200 req/min ceiling with contact info in the
User-Agent (WIKIPEDIA_CONTACT_EMAIL) - both validated in
prototypes/scripts/wikipedia_batch_action_api_poc.py. Stores the full,
untruncated extract text per species (truncation to the embedding model's
256-token limit happens later, at embed time - see WORK_SUMMARY_201126.md's
24 Nov decisions).

Checkpointing: results are appended to output_path (JSONL) one batch at a
time, flushed immediately, so a crash mid-run leaves every already-fetched
species on disk. A restart re-reads output_path and only re-fetches
specieskeys not already present - no chunk/offset tracking beyond that,
since a full run is only expected to take ~1h.
"""

import concurrent.futures
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable

import duckdb
import requests

logger = logging.getLogger("wikipedia_extracts_fetch")

MANIFEST_COLUMNS = ["specieskey", "species", "common_name", "total_occurrence", "matched_title"]
BATCH_SIZE = 20  # confirmed server max for prop=extracts (limits.extracts in the response)
TARGET_RATE_PER_MIN = 200  # Wikimedia's documented ceiling
DEFAULT_MAX_WORKERS = 3
ACTION_API_URL = "https://en.wikipedia.org/w/api.php"
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 1


class JsonLogFormatter(logging.Formatter):
    """Duplicated from jobs/monthly_gbif_update.py rather than shared - each
    job script is a separate, independently runnable process."""

    def format(self, record: logging.LogRecord) -> str:
        reserved = set(logging.makeLogRecord({}).__dict__.keys())
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in reserved})
        return json.dumps(payload)


def configure_json_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def load_manifest(manifest_path: str) -> list[dict]:
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT {', '.join(MANIFEST_COLUMNS)} FROM read_parquet('{manifest_path}')"
    ).fetchall()
    return [dict(zip(MANIFEST_COLUMNS, row, strict=True)) for row in rows]


class RateLimiter:
    """Thread-safe pacing to at most rate_per_min calls/minute, shared across
    a ThreadPoolExecutor's workers - each wait() call blocks until its own
    slot arrives, so concurrent callers still land at the paced interval
    rather than bursting."""

    def __init__(self, rate_per_min: int):
        self.min_interval = 60.0 / rate_per_min
        self._lock = threading.Lock()
        self._next_slot = time.perf_counter()

    def wait(self) -> None:
        slot = self._reserve_slot()
        delay = slot - time.perf_counter()
        if delay > 0:
            time.sleep(delay)

    def _reserve_slot(self) -> float:
        """Allocates and returns this caller's own absolute slot time
        (perf_counter units) - split out from wait() so tests can verify
        slot allocation is race-free without depending on real sleep timing
        across threads."""
        with self._lock:
            now = time.perf_counter()
            start = max(now, self._next_slot)
            self._next_slot = start + self.min_interval
        return start


def records_to_fetch(records: list[dict], already_fetched: set[str]) -> list[dict]:
    return [record for record in records if record["specieskey"] not in already_fetched]


def read_fetched_specieskeys(output_path: str) -> set[str]:
    if not os.path.exists(output_path):
        return set()
    with open(output_path) as f:
        return {json.loads(line)["specieskey"] for line in f if line.strip()}


def append_results(output_path: str, rows: list[dict]) -> None:
    """Appends rows as JSON lines, flushed immediately - the per-batch
    checkpoint a crashed run resumes from via read_fetched_specieskeys."""
    with open(output_path, "a") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()


def fetch_extracts_for_batch(records: list[dict], fetch_fn) -> list[dict]:
    """Fetches extracts for one batch of manifest records (fetch_fn takes
    the batch's titles and returns the raw Action API JSON) and attaches
    each record's resolved status/extract to its own manifest fields."""
    titles = [record["matched_title"] for record in records]
    data = fetch_fn(titles)
    resolved = resolve_batch_response(data, titles)
    return [
        {**record, "status": resolved[record["matched_title"]]["status"],
         "extract": resolved[record["matched_title"]]["extract"]}
        for record in records
    ]


def resolve_batch_response(data: dict, requested_titles: list[str]) -> dict[str, dict]:
    """Maps each originally-requested title to its final extract/status,
    following the normalized -> redirects -> pages chain the Action API
    returns. A title can be renamed twice (normalized then redirected)
    before landing on a pages entry."""
    query = data.get("query", {})
    pages_by_title = {page.get("title"): page for page in query.get("pages", {}).values()}

    current = {title: title for title in requested_titles}
    for normalized in query.get("normalized", []):
        for orig, cur in list(current.items()):
            if cur == normalized["from"]:
                current[orig] = normalized["to"]
    for redirect in query.get("redirects", []):
        for orig, cur in list(current.items()):
            if cur == redirect["from"]:
                current[orig] = redirect["to"]

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
            resolved[orig_title] = {
                "status": "ok",
                "extract": page["extract"],
                "final_title": final_title,
            }
    return resolved


def _build_user_agent() -> str:
    contact = os.environ.get("WIKIPEDIA_CONTACT_EMAIL")
    return (
        f"nature-quest/0.1 ({contact})"
        if contact
        else "nature-quest/0.1 (contact set via WIKIPEDIA_CONTACT_EMAIL env var)"
    )


def _default_action_api_fetch(titles: list[str]) -> dict:
    """Real network call - validated in
    prototypes/scripts/wikipedia_batch_action_api_poc.py (1,000/1,000 OK, 0
    failures, 0 429s at this exact request shape/pacing). Not unit tested
    directly, same as monthly_gbif_update.py's _default_checklistbank_fetch -
    run() takes fetch_fn so tests inject a fake instead."""
    params = {
        "action": "query",
        "prop": "extracts",
        "exintro": "1",
        "explaintext": "1",
        "exlimit": "max",
        "redirects": "1",
        "format": "json",
        "titles": "|".join(titles),
    }
    headers = {"User-Agent": _build_user_agent()}
    response = None
    for attempt in range(MAX_RETRIES + 1):
        response = requests.get(ACTION_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429 and attempt < MAX_RETRIES:
            retry_after = response.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after else 5.0)
            continue
        break
    assert response is not None
    response.raise_for_status()
    return response.json()


def check_output_consistency(manifest_specieskeys: set[str], output_path: str) -> dict:
    """Reads output_path back from disk (not in-memory run() counters) and
    compares its actual row/distinct-specieskey counts against the
    manifest's total - the final confirmation that what's really on disk
    matches the ~243k-species target, not just what this run's own
    bookkeeping believed it wrote."""
    with open(output_path) as f:
        row_count = sum(1 for line in f if line.strip())
    output_specieskeys = read_fetched_specieskeys(output_path)
    return {
        "manifest_species": len(manifest_specieskeys),
        "output_row_count": row_count,
        "output_distinct_specieskeys": len(output_specieskeys),
        "rows_match_distinct_keys": row_count == len(output_specieskeys),
        "output_covers_manifest": output_specieskeys == manifest_specieskeys,
    }


def run(
    manifest_path: str,
    output_path: str,
    fetch_fn: Callable[[list[str]], dict] = _default_action_api_fetch,
    batch_size: int = BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    rate_limiter: RateLimiter | None = None,
) -> dict:
    rate_limiter = rate_limiter or RateLimiter(TARGET_RATE_PER_MIN)
    records = load_manifest(manifest_path)
    already_fetched = read_fetched_specieskeys(output_path)
    remaining = records_to_fetch(records, already_fetched)
    batches = chunk(remaining, batch_size)
    logger.info(
        "wikipedia_extracts_fetch_starting",
        extra={
            "total_species": len(records),
            "already_fetched": len(already_fetched),
            "remaining": len(remaining),
            "batches": len(batches),
        },
    )
    print(
        f"Starting fetch: {len(records)} total species, {len(already_fetched)} already fetched, "
        f"{len(remaining)} remaining in {len(batches)} batches",
        flush=True,
    )

    def process_batch(batch: list[dict]) -> list[dict]:
        rate_limiter.wait()
        rows = fetch_extracts_for_batch(batch, fetch_fn=fetch_fn)
        append_results(output_path, rows)
        return rows

    ok = 0
    not_ok = 0
    batches_done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for rows in executor.map(process_batch, batches):
            batches_done += 1
            for row in rows:
                if row["status"] == "ok":
                    ok += 1
                else:
                    not_ok += 1
            species_done = len(already_fetched) + ok + not_ok
            percent_complete = round(species_done / len(records) * 100, 1) if records else 0.0
            logger.info(
                "wikipedia_extracts_fetch_progress",
                extra={
                    "batches_done": batches_done,
                    "total_batches": len(batches),
                    "species_done": species_done,
                    "total_species": len(records),
                    "percent_complete": percent_complete,
                },
            )
            print(
                f"[batch {batches_done}/{len(batches)}] species fetched: "
                f"{species_done}/{len(records)} ({percent_complete}%)",
                flush=True,
            )

    summary = {
        "total_species": len(records),
        "already_fetched": len(already_fetched),
        "fetched_this_run": ok + not_ok,
        "ok": ok,
        "not_ok": not_ok,
    }
    logger.info("wikipedia_extracts_fetch_complete", extra=summary)

    manifest_specieskeys = {record["specieskey"] for record in records}
    consistency = check_output_consistency(manifest_specieskeys, output_path)
    logger.info("wikipedia_extracts_fetch_consistency_check", extra=consistency)
    print(
        f"Consistency check: {consistency['output_row_count']} output rows, "
        f"{consistency['output_distinct_specieskeys']} distinct specieskeys, "
        f"manifest target {consistency['manifest_species']} "
        f"(covers manifest: {consistency['output_covers_manifest']})",
        flush=True,
    )

    return summary


def main() -> None:
    configure_json_logging()
    manifest_path = os.environ["WIKIPEDIA_EXTRACTS_MANIFEST_PATH"]
    output_path = os.environ["WIKIPEDIA_EXTRACTS_OUTPUT_PATH"]
    run(manifest_path, output_path)


if __name__ == "__main__":
    main()
