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


def extract_fact_and_dimension(client: bigquery.Client) -> tuple[list[dict], list[dict]]:
    """Runs one BigQuery session: a single scan of the raw public
    occurrences table into a TEMP TABLE, then two cheap derived SELECTs
    against it (fact rows, dimension rows) so the 393GB raw-table scan
    only ever happens once per run."""
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
    fact_rows = [dict(row.items()) for row in fact_job.result()]

    dimension_job = client.query(_DIMENSION_SELECT_SQL, job_config=session_job_config)
    dimension_rows = [dict(row.items()) for row in dimension_job.result()]

    return fact_rows, dimension_rows
