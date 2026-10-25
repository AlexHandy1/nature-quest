"""One-off manual backfill for the common-names cache gap fixed alongside
this script (see monthly_gbif_update.py's fetch/merge code and
docs/decisions/ADR-019 for the full write-up): new_entrants() only ever
recognised a specieskey as "already looked up" when the cache held a real
name for it, so every species ChecklistBank genuinely has no English name
for looked identical to one that had never been tried - the same ~47k-
species batch was being re-fetched and silently discarded every run.

This script closes that gap for species already looked up in a prior run,
without making any new ChecklistBank calls. It trusts that prior run's
completed result: no exception was logged during it, and each per-taxon
fetch call raises on any HTTP error, so a clean run with nothing logged
means every one of those lookups genuinely returned empty, not that it
silently failed. It records them in the cache as common_name=NULL -
present and distinct from "never checked", but still falling back to the
scientific name everywhere downstream exactly as "never checked" already
does.

Run once, manually, after a confirmed-clean prior run left this gap:
    INDEX_DATA_BUCKET=<bucket> python -m jobs.backfill_common_names_negative_cache
"""

import os
import tempfile

import duckdb
from google.cloud import storage  # type: ignore[attr-defined,import-untyped]

from jobs.monthly_gbif_update import (
    CACHE_BLOB_PATH,
    FACT_FILENAME,
    compute_top_n_specieskeys,
    download_blob_to_file,
    new_entrants,
    read_parquet_rows,
    upload_file,
    write_parquet,
)


def backfill_negative_cache(storage_client: storage.Client, bucket_name: str, workdir: str) -> dict:
    """Downloads the current production fact file and common-names cache,
    recomputes the top-N specieskey set, and writes back a cache that also
    has an explicit common_name=NULL row for every top-N specieskey that's
    missing from the cache today - no ChecklistBank calls. Returns a
    summary dict for the caller to log/print."""
    fact_path = f"{workdir}/{FACT_FILENAME}"
    cache_path = f"{workdir}/{CACHE_BLOB_PATH}"
    download_blob_to_file(storage_client, bucket_name, FACT_FILENAME, fact_path)
    download_blob_to_file(storage_client, bucket_name, CACHE_BLOB_PATH, cache_path)

    con = duckdb.connect()
    top_n = compute_top_n_specieskeys(con, fact_path)
    cache = {row["specieskey"]: row["common_name"] for row in read_parquet_rows(cache_path)}
    missing = new_entrants(top_n, cache)

    # cache isn't necessarily a subset of top_n - a species cached in a
    # prior run can drop out of this run's top_n if its occurrence rank
    # shifted slightly, and stays in the cache regardless (harmless,
    # unused this run). The invariant that must hold is narrower: every
    # top_n key is either already in the cache or in missing, never both.
    overlap = len(cache.keys() & set(top_n))
    assert overlap + len(missing) == len(top_n), "every top_n key must be in cache or missing, not neither/both"

    rows = [{"specieskey": k, "common_name": v} for k, v in cache.items()]
    rows += [{"specieskey": k, "common_name": None} for k in missing]

    new_cache_path = f"{workdir}/backfilled_{CACHE_BLOB_PATH}"
    write_parquet(rows, new_cache_path)
    upload_file(storage_client, bucket_name, CACHE_BLOB_PATH, new_cache_path)

    return {
        "top_n_count": len(top_n),
        "prior_hits": len(cache),
        "stale_cache_entries_outside_top_n": len(cache) - overlap,
        "backfilled_as_confirmed_empty": len(missing),
    }


def main() -> None:
    bucket_name = os.environ["INDEX_DATA_BUCKET"]
    workdir = tempfile.mkdtemp()
    summary = backfill_negative_cache(storage.Client(), bucket_name, workdir)
    print(summary)


if __name__ == "__main__":
    main()
