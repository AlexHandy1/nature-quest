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

import json
import os


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
