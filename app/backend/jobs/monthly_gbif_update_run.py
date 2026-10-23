import logging
from datetime import UTC, datetime

import requests

from jobs.monthly_gbif_update_bigquery_extract import extract_fact_and_dimension
from jobs.monthly_gbif_update_common_names_cache import (
    bootstrap_cache_from_production,
    compute_top_n_specieskeys,
    merge_into_cache,
    new_entrants,
)
from jobs.monthly_gbif_update_common_names_fetch import (
    CHECKLISTBANK_COL_XR_DATASET_KEY,
    build_common_name_map,
    fetch_common_names_for_taxa,
)
from jobs.monthly_gbif_update_gcs_release import (
    download_blob_to_file,
    join_common_names,
    promote_release,
    read_parquet_rows,
    release_blob_path,
    write_parquet,
)
from jobs.monthly_gbif_update_guardrails import (
    GuardrailOutcome,
    RunStats,
    compute_stats_from_parquet,
    evaluate,
)
from jobs.monthly_gbif_update_logging import log_monthly_gbif_update_outcome

FACT_FILENAME = "fact_sorted_by_cell.parquet"
RAW_DIMENSION_FILENAME = "raw_dimension.parquet"
DIMENSION_FILENAME = "species_dimension_with_common_names.parquet"
CACHE_BLOB_PATH = "common_names_cache.parquet"

logger = logging.getLogger("monthly_gbif_update")


def _default_checklistbank_fetch(dataset_key: int, taxon_id: str) -> list[dict]:
    resp = requests.get(
        f"https://api.checklistbank.org/dataset/{dataset_key}/taxon/{taxon_id}/vernacular",
        params={"lang": "eng"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run(
    bigquery_client,
    storage_client,
    con,
    bucket_name: str,
    workdir: str,
    release_date: str | None = None,
    fetch_fn=_default_checklistbank_fetch,
) -> GuardrailOutcome:
    """con must already be configured with a write-capable GCS secret
    (jobs.monthly_gbif_update_gcs_release.open_gcs_write_connection) - the
    fact table (~221M rows) and dimension table never pass through Python
    as row data; only the small common-names cache (~100K entries) does."""
    release_date = release_date or datetime.now(UTC).date().isoformat()

    prod_fact_uri = f"gs://{bucket_name}/{FACT_FILENAME}"
    prod_dim_uri = f"gs://{bucket_name}/{DIMENSION_FILENAME}"
    release_fact_uri = f"gs://{bucket_name}/{release_blob_path(release_date, FACT_FILENAME)}"
    release_raw_dim_uri = f"gs://{bucket_name}/{release_blob_path(release_date, RAW_DIMENSION_FILENAME)}"
    release_dim_uri = f"gs://{bucket_name}/{release_blob_path(release_date, DIMENSION_FILENAME)}"

    previous = compute_stats_from_parquet(con, prod_fact_uri, prod_dim_uri)

    fact_stats, dimension_stats = extract_fact_and_dimension(
        bigquery_client, release_fact_uri, release_raw_dim_uri
    )

    cache_path = f"{workdir}/{CACHE_BLOB_PATH}"
    try:
        download_blob_to_file(storage_client, bucket_name, CACHE_BLOB_PATH, cache_path)
        cache = {row["specieskey"]: row["common_name"] for row in read_parquet_rows(cache_path)}
    except Exception:  # noqa: BLE001 - deliberate: no cache yet (first run) falls back to bootstrap
        cache = bootstrap_cache_from_production(con, prod_dim_uri)

    try:
        top_n = compute_top_n_specieskeys(con, release_fact_uri)
        entrants = new_entrants(top_n, cache)
        records = fetch_common_names_for_taxa(
            CHECKLISTBANK_COL_XR_DATASET_KEY, entrants, fetch_fn=fetch_fn
        )
        cache = merge_into_cache(cache, build_common_name_map(records))
    except Exception:
        logger.exception("Common-names fetch failed - continuing with the existing cache")

    if cache:
        new_cache_path = f"{workdir}/new_{CACHE_BLOB_PATH}"
        write_parquet([{"specieskey": k, "common_name": v} for k, v in cache.items()], new_cache_path)
        storage_client.bucket(bucket_name).blob(CACHE_BLOB_PATH).upload_from_filename(new_cache_path)

    join_common_names(con, release_raw_dim_uri, cache, release_dim_uri)

    current = RunStats(
        fact_columns=fact_stats["fact_columns"],
        dimension_columns=dimension_stats["dimension_columns"] | {"common_name"},
        total_occurrence_count=fact_stats["total_occurrence_count"],
        distinct_cell_count=fact_stats["distinct_cell_count"],
        distinct_species_count=dimension_stats["distinct_species_count"],
    )
    outcome = evaluate(current, previous)
    log_monthly_gbif_update_outcome(outcome.verdict, outcome.block_reasons, outcome.warn_reasons)

    if outcome.verdict != "block":
        promote_release(storage_client, bucket_name, release_date, [FACT_FILENAME, DIMENSION_FILENAME])

    return outcome
