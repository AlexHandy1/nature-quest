"""Monthly GBIF data-update pipeline - the Cloud Run Job entrypoint.

Flow: generate a target release from BigQuery (with its own summary
stats) -> read current production's summary stats -> compare guardrails
-> if not blocked, join common names and promote the release to
production. See docs/decisions/ADR-016/017 and
docs/status_docs/PLANNING_DUCKDB_MIGRATION_201026.md for the surrounding
architecture this automates (previously a manual, one-off process).
"""

import concurrent.futures
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Literal

import duckdb
import pandas as pd
import requests
from google.cloud import bigquery, storage  # type: ignore[attr-defined]

logger = logging.getLogger("monthly_gbif_update")

# --- File/path layout --------------------------------------------------

FACT_FILENAME = "fact_sorted_by_cell.parquet"
RAW_DIMENSION_FILENAME = "raw_dimension.parquet"
DIMENSION_FILENAME = "species_dimension_with_common_names.parquet"
CACHE_BLOB_PATH = "common_names_cache.parquet"


def release_blob_path(release_date: str, filename: str) -> str:
    return f"releases/{release_date}/{filename}"


# --- Logging -------------------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """Renders log records as one JSON line per entry, so Cloud Run's stdout
    capture parses them into structured (queryable) Cloud Logging fields
    instead of one opaque text blob. Duplicated from main.py rather than
    shared - this is a separate process that never imports main.py."""

    _RESERVED_KEYS: ClassVar[set[str]] = set(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": record.levelname, "message": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in self._RESERVED_KEYS})
        return json.dumps(payload)


def configure_json_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def log_outcome(verdict: str, block_reasons: list[str] | None = None, warn_reasons: list[str] | None = None) -> None:
    logger.info(
        "monthly_gbif_update_outcome",
        extra={"verdict": verdict, "block_reasons": block_reasons or [], "warn_reasons": warn_reasons or []},
    )


# --- Guardrails ------------------------------------------------------------

EXPECTED_FACT_COLUMNS = frozenset({"cell", "specieskey", "occurrence_count"})
EXPECTED_DIMENSION_COLUMNS = frozenset({
    "specieskey", "species", "kingdom", "phylum", "class", "order", "family", "genus", "common_name"
})
NEAR_ZERO_CHANGE_THRESHOLD_PCT = 0.1
LARGE_SWING_THRESHOLD_PCT = 20.0


@dataclass(frozen=True)
class RunStats:
    fact_columns: frozenset[str]
    dimension_columns: frozenset[str]
    total_occurrence_count: int
    distinct_cell_count: int
    distinct_species_count: int


@dataclass(frozen=True)
class GuardrailOutcome:
    verdict: Literal["pass", "warn", "block"]
    checks: list[dict] = field(default_factory=list)  # every check, including passing ones
    block_reasons: list[str] = field(default_factory=list)
    warn_reasons: list[str] = field(default_factory=list)


def compute_stats_from_parquet(con: duckdb.DuckDBPyConnection, fact_path: str, dimension_path: str) -> RunStats:
    """Derives RunStats via DuckDB aggregate queries against fact_path/
    dimension_path (local paths) - neither the ~221M-row fact table nor
    the dimension table ever passes through Python as row data."""
    fact_columns = frozenset(
        row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{fact_path}')").fetchall()
    )
    dimension_columns = frozenset(
        row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{dimension_path}')").fetchall()
    )
    fact_stats_row = con.execute(f"""
        SELECT SUM(occurrence_count), COUNT(DISTINCT cell) FROM read_parquet('{fact_path}')
    """).fetchone()
    assert fact_stats_row is not None
    total_occurrence_count, distinct_cell_count = fact_stats_row

    dimension_stats_row = con.execute(f"""
        SELECT COUNT(DISTINCT specieskey) FROM read_parquet('{dimension_path}')
    """).fetchone()
    assert dimension_stats_row is not None
    (distinct_species_count,) = dimension_stats_row
    return RunStats(
        fact_columns=fact_columns,
        dimension_columns=dimension_columns,
        total_occurrence_count=total_occurrence_count,
        distinct_cell_count=distinct_cell_count,
        distinct_species_count=distinct_species_count,
    )


def _schema_check(name: str, missing_columns: frozenset[str], table_label: str) -> dict:
    if missing_columns:
        detail = f"{table_label} missing expected columns: {sorted(missing_columns)}"
        return {"check": name, "status": "block", "detail": detail}
    return {"check": name, "status": "ok", "detail": None}


def _count_check(label: str, baseline_value: int, release_value: int) -> dict:
    if release_value < baseline_value:
        detail = f"{label} decreased: {baseline_value} -> {release_value}"
        return {"check": label, "status": "block", "detail": detail}
    change = _percent_change(baseline_value, release_value)
    if change < NEAR_ZERO_CHANGE_THRESHOLD_PCT:
        detail = f"{label} essentially unchanged: {baseline_value} -> {release_value}"
        return {"check": label, "status": "warn", "detail": detail}
    if change > LARGE_SWING_THRESHOLD_PCT:
        detail = f"{label} swung by more than {LARGE_SWING_THRESHOLD_PCT}%: {baseline_value} -> {release_value}"
        return {"check": label, "status": "warn", "detail": detail}
    return {"check": label, "status": "ok", "detail": f"{baseline_value} -> {release_value}"}


def evaluate(target_release: RunStats, current_production: RunStats) -> GuardrailOutcome:
    """Every check (schema x2, count metrics x3) gets an explicit status
    every time, not just failures - so the full picture is always visible,
    not only what went wrong."""
    checks = [
        _schema_check("fact schema", EXPECTED_FACT_COLUMNS - target_release.fact_columns, "fact table"),
        _schema_check(
            "dimension schema", EXPECTED_DIMENSION_COLUMNS - target_release.dimension_columns, "dimension table"
        ),
        _count_check(
            "total occurrence count",
            current_production.total_occurrence_count,
            target_release.total_occurrence_count,
        ),
        _count_check(
            "distinct cell count", current_production.distinct_cell_count, target_release.distinct_cell_count
        ),
        _count_check(
            "distinct species count",
            current_production.distinct_species_count,
            target_release.distinct_species_count,
        ),
    ]

    block_reasons = [check["detail"] for check in checks if check["status"] == "block"]
    warn_reasons = [check["detail"] for check in checks if check["status"] == "warn"]

    if block_reasons:
        verdict: Literal["pass", "warn", "block"] = "block"
    elif warn_reasons:
        verdict = "warn"
    else:
        verdict = "pass"
    return GuardrailOutcome(verdict=verdict, checks=checks, block_reasons=block_reasons, warn_reasons=warn_reasons)


def _percent_change(baseline_value: int, release_value: int) -> float:
    if baseline_value == 0:
        return 0.0
    return abs(release_value - baseline_value) / baseline_value * 100


# --- BigQuery: generate the target release ---------------------------------

# Free-tier ceiling (1TB/month on-demand) used as a cost circuit-breaker,
# not a realistic month-to-month threshold - see
# docs/decisions/ADR-016-local-index-data-deployment-gcs-fetch-not-bake-in.md.
MAXIMUM_BYTES_BILLED = 1_000_000_000_000
RAW_OCCURRENCES_TABLE = "bigquery-public-data.gbif.occurrences"
TOP_N_COVERAGE = 0.99

_SCAN_INTO_TEMP_TABLE_SQL = f"""
    CREATE TEMP TABLE agg AS
    SELECT
        ST_GEOHASH(ST_GEOGPOINT(decimallongitude, decimallatitude), 6) AS cell,
        specieskey, species, kingdom, phylum, class, `order`, family, genus
    FROM `{RAW_OCCURRENCES_TABLE}`
    WHERE occurrencestatus = 'PRESENT'
      AND specieskey IS NOT NULL
      AND decimallatitude IS NOT NULL
      AND decimallongitude IS NOT NULL
"""

_FACT_SELECT_SQL = """
    SELECT cell, specieskey, COUNT(*) AS occurrence_count
    FROM agg
    GROUP BY cell, specieskey
    HAVING occurrence_count > 1
    ORDER BY cell
"""

_DIMENSION_SELECT_SQL = """
    SELECT DISTINCT specieskey, species, kingdom, phylum, class, `order`, family, genus
    FROM agg
"""


def _run_fact_and_dimension_queries(
    client: bigquery.Client,
) -> tuple[bigquery.TableReference, bigquery.TableReference]:
    """One BigQuery session: a single scan of the raw public occurrences
    table into a TEMP TABLE, then two cheap derived SELECTs against it
    (fact, dimension) - so the 393GB raw-table scan only ever happens
    once per run."""
    session_job_config = bigquery.QueryJobConfig(create_session=True, maximum_bytes_billed=MAXIMUM_BYTES_BILLED)
    scan_job = client.query(_SCAN_INTO_TEMP_TABLE_SQL, job_config=session_job_config)
    scan_job.result()
    session_info = scan_job.session_info
    assert session_info is not None, "BigQuery did not return a session for create_session=True"
    session_id = session_info.session_id
    assert session_id is not None, "BigQuery session has no session_id"

    session_job_config = bigquery.QueryJobConfig(
        connection_properties=[bigquery.ConnectionProperty("session_id", session_id)]
    )
    fact_job = client.query(_FACT_SELECT_SQL, job_config=session_job_config)
    fact_job.result()
    dimension_job = client.query(_DIMENSION_SELECT_SQL, job_config=session_job_config)
    dimension_job.result()
    return fact_job.destination, dimension_job.destination


def _compute_top_n_specieskeys(
    client: bigquery.Client, fact_table_ref: bigquery.TableReference, coverage: float = TOP_N_COVERAGE
) -> list[str]:
    """Smallest set of specieskeys (by total occurrence_count summed
    across cells) accounting for at least `coverage` of all recorded
    occurrences - computed server-side against the fact destination
    table, so this never needs to read the extracted GCS file back."""
    sql = f"""
        WITH species_totals AS (
            SELECT specieskey, SUM(occurrence_count) AS total_occ
            FROM `{fact_table_ref}`
            GROUP BY specieskey
        ),
        ranked AS (
            SELECT specieskey, total_occ,
                   SUM(total_occ) OVER (ORDER BY total_occ DESC ROWS UNBOUNDED PRECEDING) AS cum_occ
            FROM species_totals
        ),
        grand_total AS (SELECT SUM(total_occ) AS t FROM species_totals)
        SELECT specieskey
        FROM ranked, grand_total
        WHERE cum_occ - total_occ < {coverage} * t
        ORDER BY cum_occ
    """
    return [row["specieskey"] for row in client.query(sql).result()]


def _extract_table_to_gcs(client: bigquery.Client, table_ref: bigquery.TableReference, destination_uri: str) -> None:
    extract_job = client.extract_table(
        table_ref, destination_uri, job_config=bigquery.ExtractJobConfig(destination_format="PARQUET")
    )
    extract_job.result()


def generate_target_release(
    client: bigquery.Client, fact_destination_uri: str, dimension_destination_uri: str
) -> tuple[dict, dict, list[str]]:
    """Runs the fact/dimension derivation and extracts both results
    directly to GCS (BigQuery's own server-side export), then returns
    their schema + counts as plain stats dicts, plus the top-N
    specieskeys by occurrence volume - row data for either table (the
    fact table alone is ~221M rows) never passes through Python."""
    fact_table_ref, dimension_table_ref = _run_fact_and_dimension_queries(client)

    _extract_table_to_gcs(client, fact_table_ref, fact_destination_uri)
    _extract_table_to_gcs(client, dimension_table_ref, dimension_destination_uri)

    fact_table = client.get_table(fact_table_ref)
    dimension_table = client.get_table(dimension_table_ref)

    fact_stats_row = next(
        iter(
            client.query(f"""
        SELECT SUM(occurrence_count) AS total_occurrence_count, COUNT(DISTINCT cell) AS distinct_cell_count
        FROM `{fact_table_ref}`
    """).result()
        )
    )
    dimension_stats_row = next(
        iter(
            client.query(
                f"SELECT COUNT(DISTINCT specieskey) AS distinct_species_count FROM `{dimension_table_ref}`"
            ).result()
        )
    )

    fact_stats = {
        "fact_columns": frozenset(field.name for field in fact_table.schema),
        "total_occurrence_count": fact_stats_row["total_occurrence_count"],
        "distinct_cell_count": fact_stats_row["distinct_cell_count"],
    }
    dimension_stats = {
        "dimension_columns": frozenset(field.name for field in dimension_table.schema),
        "distinct_species_count": dimension_stats_row["distinct_species_count"],
    }
    top_n_specieskeys = _compute_top_n_specieskeys(client, fact_table_ref)
    return fact_stats, dimension_stats, top_n_specieskeys


# --- GCS: local Parquet I/O and promotion -----------------------------------


def write_parquet(rows: list[dict], path: str) -> None:
    con = duckdb.connect()
    con.register("rows_df", pd.DataFrame(rows))
    con.execute(f"COPY rows_df TO '{path}' (FORMAT PARQUET)")


def read_parquet_rows(path: str) -> list[dict]:
    con = duckdb.connect()
    columns = [col[0] for col in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    rows = con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def download_blob_to_file(client: storage.Client, bucket_name: str, blob_path: str, local_path: str) -> None:
    client.bucket(bucket_name).blob(blob_path).download_to_filename(local_path)


def upload_file(client: storage.Client, bucket_name: str, blob_path: str, local_path: str) -> None:
    client.bucket(bucket_name).blob(blob_path).upload_from_filename(local_path)


def promote_release(client: storage.Client, bucket_name: str, release_date: str, filenames: list[str]) -> None:
    """Copies each dated release object onto the fixed root path the live
    app reads from (same filename, bucket root) - the one action that
    actually makes a new release live."""
    bucket = client.bucket(bucket_name)
    for filename in filenames:
        source_blob = bucket.blob(release_blob_path(release_date, filename))
        bucket.copy_blob(source_blob, bucket, filename)


# --- Common names: cache and ChecklistBank fetch ----------------------------

DEFAULT_MAX_WORKERS = 20

# Pinned explicitly, not resolved live on every run. Why: our fact/
# dimension specieskeys come from whatever COL XR release GBIF had pinned
# at BigQuery's snapshot time; resolving this live at pipeline-run time
# could silently drift to a newer release than the one our occurrence data
# actually uses. Update this constant only via a deliberate decision - the
# same way the Testudines rank-override and the COL XR migration itself
# (WORK_SUMMARY_091026.md) were handled, never automatically.
#
# Verified against GBIF's registry 2026-10-23: resolves to ChecklistBank
# dataset 315557, alias "COL26.6 XR", version "2026-06-19 XR" - Catalogue
# of Life's Extended Release. To re-check: GET
# https://api.gbif.org/v1/dataset/7ddf754f-d193-4cc9-b351-99906754a03b and
# read the identifiers[].identifier where type is "CLB_DATASET_KEY", then
# GET https://api.checklistbank.org/dataset/{that key} and compare its
# alias/version against the ones recorded here.
CHECKLISTBANK_COL_XR_DATASET_KEY = 315557


def new_entrants(top_n_specieskeys: list[str], existing_cache: dict[str, str]) -> list[str]:
    return [key for key in top_n_specieskeys if key not in existing_cache]


def merge_into_cache(existing_cache: dict[str, str], newly_fetched: dict[str, str]) -> dict[str, str]:
    return {**newly_fetched, **existing_cache}


def bootstrap_cache_from_production(con: duckdb.DuckDBPyConnection, dimension_path: str) -> dict[str, str]:
    """One-time initialization when no cache file exists yet: seeds the
    cache from whatever common_name values are already in the current
    production dimension file, instead of starting empty and refetching
    everything via ChecklistBank."""
    rows = con.execute(f"""
        SELECT specieskey, common_name FROM read_parquet('{dimension_path}')
        WHERE common_name IS NOT NULL
    """).fetchall()
    return dict(rows)


def join_common_names(
    con: duckdb.DuckDBPyConnection, dimension_path: str, cache: dict[str, str], output_path: str
) -> None:
    """Joins the common-names cache onto the dimension table entirely
    within DuckDB on local paths - dimension rows never pass through
    Python regardless of table size."""
    cache_df = pd.DataFrame({"specieskey": list(cache.keys()), "common_name": list(cache.values())})
    con.register("cache_df", cache_df)
    con.execute(f"""
        COPY (
            SELECT d.*, c.common_name
            FROM read_parquet('{dimension_path}') d
            LEFT JOIN cache_df c ON d.specieskey = c.specieskey
        ) TO '{output_path}' (FORMAT PARQUET)
    """)


def _is_code_like(name: str) -> bool:
    """Looks like a banding/abbreviation code (e.g. "COST", "GRTI"), not a name."""
    stripped = name.replace(" ", "").replace("-", "").replace(".", "")
    return stripped.isalpha() and stripped.isupper() and len(stripped) <= 5


def _is_informal_lowercase(name: str) -> bool:
    """Informal/slang entries in ChecklistBank's crowd-sourced data start
    lowercase (e.g. "pigeon", "kildeer"), unlike real English common names."""
    return name[0].islower()


def _is_non_ascii(name: str) -> bool:
    """Catches names in another script mistagged as English in the source
    data (e.g. an Indigenous-language name tagged language=eng)."""
    return not name.isascii()


def _is_junk(name: str) -> bool:
    return _is_code_like(name) or _is_informal_lowercase(name) or _is_non_ascii(name)


def pick_best_name(names: list[str]) -> str:
    candidates = [name for name in names if not _is_junk(name)]
    if not candidates:
        candidates = names
    return min(candidates, key=lambda name: (-len(name), name))


def build_common_name_map(records: list[dict]) -> dict[str, str]:
    names_by_taxon_id: dict[str, list[str]] = {}
    for record in records:
        names_by_taxon_id.setdefault(record["taxonID"], []).append(record["name"])
    return {taxon_id: pick_best_name(names) for taxon_id, names in names_by_taxon_id.items()}


def fetch_common_names_for_taxa(
    dataset_key: int,
    taxon_ids: list[str],
    fetch_fn: Callable[[int, str], list[dict]],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[dict]:
    """Fans out one request per taxon, concurrently (bounded by max_workers).
    fetch_fn is injected so this is testable without a network call - a
    failed fetch here is non-blocking for the pipeline (the existing
    common-names cache already provides durable coverage)."""
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_fn, dataset_key, taxon_id): taxon_id for taxon_id in taxon_ids}
        for future in concurrent.futures.as_completed(futures):
            taxon_id = futures[future]
            for name_row in future.result():
                records.append({"taxonID": taxon_id, "name": name_row["name"]})
    return records


def _default_checklistbank_fetch(dataset_key: int, taxon_id: str) -> list[dict]:
    resp = requests.get(
        f"https://api.checklistbank.org/dataset/{dataset_key}/taxon/{taxon_id}/vernacular",
        params={"lang": "eng"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# --- Orchestration -----------------------------------------------------------


def run(
    bigquery_client: bigquery.Client,
    storage_client: storage.Client,
    con: duckdb.DuckDBPyConnection,
    bucket_name: str,
    workdir: str,
    release_date: str | None = None,
    fetch_fn: Callable[[int, str], list[dict]] = _default_checklistbank_fetch,
) -> GuardrailOutcome:
    """con is a plain local DuckDB connection (duckdb.connect()) - no GCS
    credentials needed, since every DuckDB query in this run touches only
    locally-downloaded files, never gs:// paths directly."""
    release_date = release_date or datetime.now(UTC).date().isoformat()

    # 1. Generate the target release in BigQuery and its summary stats.
    logger.info("Step 1/5: generating target release from BigQuery", extra={"release_date": release_date})
    release_fact_uri = f"gs://{bucket_name}/{release_blob_path(release_date, FACT_FILENAME)}"
    release_raw_dim_uri = f"gs://{bucket_name}/{release_blob_path(release_date, RAW_DIMENSION_FILENAME)}"
    target_fact_stats, target_dimension_stats, top_n_specieskeys = generate_target_release(
        bigquery_client, release_fact_uri, release_raw_dim_uri
    )

    # 2. Read current production's summary stats.
    logger.info("Step 2/5: reading current production baseline")
    prod_fact_path = f"{workdir}/prod_{FACT_FILENAME}"
    prod_dim_path = f"{workdir}/prod_{DIMENSION_FILENAME}"
    download_blob_to_file(storage_client, bucket_name, FACT_FILENAME, prod_fact_path)
    download_blob_to_file(storage_client, bucket_name, DIMENSION_FILENAME, prod_dim_path)
    current_production_stats = compute_stats_from_parquet(con, prod_fact_path, prod_dim_path)

    # 3. Compare guardrails.
    logger.info("Step 3/5: evaluating guardrails")
    target_release_stats = RunStats(
        fact_columns=target_fact_stats["fact_columns"],
        dimension_columns=target_dimension_stats["dimension_columns"] | {"common_name"},
        total_occurrence_count=target_fact_stats["total_occurrence_count"],
        distinct_cell_count=target_fact_stats["distinct_cell_count"],
        distinct_species_count=target_dimension_stats["distinct_species_count"],
    )
    outcome = evaluate(target_release_stats, current_production_stats)
    for check in outcome.checks:
        logger.info(
            f"Guardrail check: {check['check']} -> {check['status']}", extra={"detail": check["detail"]}
        )
    log_outcome(outcome.verdict, outcome.block_reasons, outcome.warn_reasons)

    if outcome.verdict == "block":
        logger.info("Guardrails blocked this release - stopping before the common-names join and promote")
        return outcome

    # 4. Common names join.
    logger.info("Step 4/5: joining common names")
    cache_path = f"{workdir}/{CACHE_BLOB_PATH}"
    try:
        download_blob_to_file(storage_client, bucket_name, CACHE_BLOB_PATH, cache_path)
        cache = {row["specieskey"]: row["common_name"] for row in read_parquet_rows(cache_path)}
    except Exception:  # noqa: BLE001 - deliberate: no cache yet (first run) falls back to bootstrap
        cache = bootstrap_cache_from_production(con, prod_dim_path)

    try:
        entrants = new_entrants(top_n_specieskeys, cache)
        records = fetch_common_names_for_taxa(CHECKLISTBANK_COL_XR_DATASET_KEY, entrants, fetch_fn=fetch_fn)
        cache = merge_into_cache(cache, build_common_name_map(records))
    except Exception:
        logger.exception("Common-names fetch failed - continuing with the existing cache")

    if cache:
        new_cache_path = f"{workdir}/new_{CACHE_BLOB_PATH}"
        write_parquet([{"specieskey": k, "common_name": v} for k, v in cache.items()], new_cache_path)
        upload_file(storage_client, bucket_name, CACHE_BLOB_PATH, new_cache_path)

    raw_dim_path = f"{workdir}/{RAW_DIMENSION_FILENAME}"
    joined_dim_path = f"{workdir}/{DIMENSION_FILENAME}"
    download_blob_to_file(
        storage_client, bucket_name, release_blob_path(release_date, RAW_DIMENSION_FILENAME), raw_dim_path
    )
    join_common_names(con, raw_dim_path, cache, joined_dim_path)
    upload_file(storage_client, bucket_name, release_blob_path(release_date, DIMENSION_FILENAME), joined_dim_path)

    # 5. Release to production by copying.
    logger.info("Step 5/5: promoting release to production")
    promote_release(storage_client, bucket_name, release_date, [FACT_FILENAME, DIMENSION_FILENAME])

    return outcome


def main() -> None:
    """Cloud Run Job entrypoint. A verdict of "block" still exits 0 - it's
    an expected, already-logged/alerted outcome, not a crash; only a real
    unhandled exception (something that prevented the guardrail decision
    from ever being reached) should surface as a failed execution and
    trip the GCP-level backstop alert."""
    configure_json_logging()
    bucket_name = os.environ["INDEX_DATA_BUCKET"]
    workdir = tempfile.mkdtemp()
    run(bigquery.Client(), storage.Client(), duckdb.connect(), bucket_name, workdir)


if __name__ == "__main__":
    main()
