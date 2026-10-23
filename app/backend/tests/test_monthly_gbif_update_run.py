from unittest.mock import MagicMock, patch

import jobs.monthly_gbif_update_run as orchestrator
from jobs.monthly_gbif_update_guardrails import RunStats
from jobs.monthly_gbif_update_run import DIMENSION_FILENAME, FACT_FILENAME, run

RAW_DIMENSION_COLUMNS = frozenset(
    {"specieskey", "species", "kingdom", "phylum", "class", "order", "family", "genus"}
)


def _healthy_stats(**overrides) -> RunStats:
    defaults = {
        "fact_columns": frozenset({"cell", "specieskey", "occurrence_count"}),
        "dimension_columns": RAW_DIMENSION_COLUMNS | {"common_name"},
        "total_occurrence_count": 1_000_000,
        "distinct_cell_count": 10_000,
        "distinct_species_count": 5_000,
    }
    defaults.update(overrides)
    return RunStats(**defaults)


def test_run_promotes_the_release_on_a_clean_pass(tmp_path):
    bigquery_client = MagicMock()
    storage_client = MagicMock()
    con = MagicMock()

    fact_stats = {
        "fact_columns": frozenset({"cell", "specieskey", "occurrence_count"}),
        "total_occurrence_count": 1_050_000,
        "distinct_cell_count": 10_500,
    }
    dimension_stats = {
        "dimension_columns": RAW_DIMENSION_COLUMNS,
        "distinct_species_count": 5_250,
    }

    with (
        patch.object(
            orchestrator, "compute_stats_from_parquet", return_value=_healthy_stats()
        ),
        patch.object(
            orchestrator, "extract_fact_and_dimension", return_value=(fact_stats, dimension_stats)
        ),
        patch.object(orchestrator, "download_blob_to_file", side_effect=FileNotFoundError),
        patch.object(orchestrator, "bootstrap_cache_from_production", return_value={}),
        patch.object(orchestrator, "compute_top_n_specieskeys", return_value=["SP1"]),
        patch.object(orchestrator, "fetch_common_names_for_taxa", return_value=[]),
        patch.object(orchestrator, "join_common_names") as mock_join,
        patch.object(orchestrator, "promote_release") as mock_promote,
    ):
        outcome = run(
            bigquery_client,
            storage_client,
            con,
            "my-bucket",
            workdir=str(tmp_path),
            release_date="2026-11-07",
        )

    assert outcome.verdict == "pass"
    mock_join.assert_called_once()
    mock_promote.assert_called_once_with(
        storage_client, "my-bucket", "2026-11-07", [FACT_FILENAME, DIMENSION_FILENAME]
    )


def test_run_does_not_promote_when_guardrails_block(tmp_path):
    bigquery_client = MagicMock()
    storage_client = MagicMock()
    con = MagicMock()

    fact_stats = {
        "fact_columns": frozenset({"cell", "specieskey", "occurrence_count"}),
        "total_occurrence_count": 500_000,
        "distinct_cell_count": 10_500,
    }
    dimension_stats = {
        "dimension_columns": RAW_DIMENSION_COLUMNS,
        "distinct_species_count": 5_250,
    }

    with (
        patch.object(
            orchestrator, "compute_stats_from_parquet", return_value=_healthy_stats()
        ),
        patch.object(
            orchestrator, "extract_fact_and_dimension", return_value=(fact_stats, dimension_stats)
        ),
        patch.object(orchestrator, "download_blob_to_file", side_effect=FileNotFoundError),
        patch.object(orchestrator, "bootstrap_cache_from_production", return_value={}),
        patch.object(orchestrator, "compute_top_n_specieskeys", return_value=["SP1"]),
        patch.object(orchestrator, "fetch_common_names_for_taxa", return_value=[]),
        patch.object(orchestrator, "join_common_names"),
        patch.object(orchestrator, "promote_release") as mock_promote,
    ):
        outcome = run(
            bigquery_client,
            storage_client,
            con,
            "my-bucket",
            workdir=str(tmp_path),
            release_date="2026-11-07",
        )

    assert outcome.verdict == "block"
    mock_promote.assert_not_called()
