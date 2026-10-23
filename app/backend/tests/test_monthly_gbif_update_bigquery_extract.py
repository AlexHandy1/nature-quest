from unittest.mock import MagicMock

from jobs.monthly_gbif_update_bigquery_extract import extract_fact_and_dimension


def _mock_field(name):
    field = MagicMock()
    field.name = name
    return field


def _mock_client():
    scan_job = MagicMock()
    scan_job.result.return_value = []

    fact_job = MagicMock()
    fact_job.result.return_value = []
    fact_job.destination = "project.dataset.fact_tmp"

    dimension_job = MagicMock()
    dimension_job.result.return_value = []
    dimension_job.destination = "project.dataset.dimension_tmp"

    extract_job = MagicMock()
    extract_job.result.return_value = None

    fact_stats_row = {"total_occurrence_count": 1_000_000, "distinct_cell_count": 10_000}
    dimension_stats_row = {"distinct_species_count": 5_000}
    fact_stats_job = MagicMock()
    fact_stats_job.result.return_value = [fact_stats_row]
    dimension_stats_job = MagicMock()
    dimension_stats_job.result.return_value = [dimension_stats_row]

    client = MagicMock()
    client.query.side_effect = [scan_job, fact_job, dimension_job, fact_stats_job, dimension_stats_job]
    client.extract_table.return_value = extract_job

    fact_table = MagicMock()
    fact_table.schema = [_mock_field("cell"), _mock_field("specieskey"), _mock_field("occurrence_count")]
    dimension_table = MagicMock()
    dimension_table.schema = [_mock_field("specieskey"), _mock_field("species")]
    client.get_table.side_effect = [fact_table, dimension_table]

    return client, extract_job


def test_extract_fact_and_dimension_extracts_both_tables_and_returns_their_stats():
    client, _extract_job = _mock_client()

    fact_stats, dimension_stats = extract_fact_and_dimension(
        client, "gs://bucket/fact.parquet", "gs://bucket/dimension.parquet"
    )

    assert fact_stats == {
        "fact_columns": frozenset({"cell", "specieskey", "occurrence_count"}),
        "total_occurrence_count": 1_000_000,
        "distinct_cell_count": 10_000,
    }
    assert dimension_stats == {
        "dimension_columns": frozenset({"specieskey", "species"}),
        "distinct_species_count": 5_000,
    }
    assert client.extract_table.call_count == 2
