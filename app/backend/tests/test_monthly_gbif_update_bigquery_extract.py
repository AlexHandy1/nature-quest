from unittest.mock import MagicMock

from jobs.monthly_gbif_update_bigquery_extract import extract_fact_and_dimension


def _mock_client(fact_rows: list[dict], dimension_rows: list[dict]) -> MagicMock:
    scan_job = MagicMock()
    scan_job.result.return_value = []

    fact_job = MagicMock()
    fact_job.result.return_value = fact_rows

    dimension_job = MagicMock()
    dimension_job.result.return_value = dimension_rows

    client = MagicMock()
    client.query.side_effect = [scan_job, fact_job, dimension_job]
    return client


def test_returns_fact_and_dimension_rows_as_plain_dicts():
    client = _mock_client(
        fact_rows=[{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}],
        dimension_rows=[{"specieskey": "SP1", "species": "Pica pica"}],
    )

    fact_rows, dimension_rows = extract_fact_and_dimension(client)

    assert fact_rows == [{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}]
    assert dimension_rows == [{"specieskey": "SP1", "species": "Pica pica"}]
