from google.cloud import bigquery

# Free-tier ceiling (1TB/month on-demand) used as a cost circuit-breaker,
# not a realistic month-to-month threshold - see
# docs/decisions/ADR-016-local-index-data-deployment-gcs-fetch-not-bake-in.md
# and the monthly-update design discussion for why this scan should never
# cost anything.
MAXIMUM_BYTES_BILLED = 1_000_000_000_000

RAW_OCCURRENCES_TABLE = "bigquery-public-data.gbif.occurrences"

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
    """Runs one BigQuery session: a single scan of the raw public
    occurrences table into a TEMP TABLE, then two cheap derived SELECTs
    against it (fact, dimension), each landing in its own job-managed
    destination table - so the 393GB raw-table scan only ever happens
    once, and the results can be extracted/aggregated afterward without
    ever pulling their row data through Python."""
    session_job_config = bigquery.QueryJobConfig(
        create_session=True, maximum_bytes_billed=MAXIMUM_BYTES_BILLED
    )
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


def _extract_table_to_gcs(
    client: bigquery.Client, table_ref: bigquery.TableReference, destination_uri: str
) -> None:
    extract_job = client.extract_table(
        table_ref, destination_uri, job_config=bigquery.ExtractJobConfig(destination_format="PARQUET")
    )
    extract_job.result()


def extract_fact_and_dimension(
    client: bigquery.Client, fact_destination_uri: str, dimension_destination_uri: str
) -> tuple[dict, dict]:
    """Runs the fact/dimension derivation and extracts both results
    directly to GCS (BigQuery's own server-side export), then returns
    their schema + counts as plain stats dicts - row data for both tables
    (the fact table alone is ~221M rows) never passes through Python."""
    fact_table_ref, dimension_table_ref = _run_fact_and_dimension_queries(client)

    _extract_table_to_gcs(client, fact_table_ref, fact_destination_uri)
    _extract_table_to_gcs(client, dimension_table_ref, dimension_destination_uri)

    fact_table = client.get_table(fact_table_ref)
    dimension_table = client.get_table(dimension_table_ref)

    fact_stats_sql = f"""
        SELECT SUM(occurrence_count) AS total_occurrence_count, COUNT(DISTINCT cell) AS distinct_cell_count
        FROM `{fact_table_ref}`
    """
    fact_stats_row = next(iter(client.query(fact_stats_sql).result()))

    dimension_stats_sql = f"SELECT COUNT(DISTINCT specieskey) AS distinct_species_count FROM `{dimension_table_ref}`"
    dimension_stats_row = next(iter(client.query(dimension_stats_sql).result()))

    fact_stats = {
        "fact_columns": frozenset(field.name for field in fact_table.schema),
        "total_occurrence_count": fact_stats_row["total_occurrence_count"],
        "distinct_cell_count": fact_stats_row["distinct_cell_count"],
    }
    dimension_stats = {
        "dimension_columns": frozenset(field.name for field in dimension_table.schema),
        "distinct_species_count": dimension_stats_row["distinct_species_count"],
    }
    return fact_stats, dimension_stats
