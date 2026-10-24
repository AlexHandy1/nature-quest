from unittest.mock import MagicMock, patch

import duckdb

import jobs.monthly_gbif_update as job
from jobs.monthly_gbif_update import (
    DIMENSION_FILENAME,
    EXPECTED_DIMENSION_COLUMNS,
    EXPECTED_FACT_COLUMNS,
    FACT_FILENAME,
    RunStats,
    bootstrap_cache_from_production,
    build_common_name_map,
    compute_stats_from_parquet,
    download_blob_to_file,
    evaluate,
    fetch_common_names_for_taxa,
    generate_target_release,
    join_common_names,
    log_outcome,
    merge_into_cache,
    new_entrants,
    pick_best_name,
    promote_release,
    read_parquet_rows,
    release_blob_path,
    run,
    upload_file,
    write_parquet,
)

# --- Guardrails --------------------------------------------------------


def _healthy_stats(**overrides) -> RunStats:
    defaults = {
        "fact_columns": EXPECTED_FACT_COLUMNS,
        "dimension_columns": EXPECTED_DIMENSION_COLUMNS,
        "total_occurrence_count": 1_000_000,
        "distinct_cell_count": 10_000,
        "distinct_species_count": 5_000,
    }
    defaults.update(overrides)
    return RunStats(**defaults)


def test_passes_when_schema_matches_and_no_counts_decreased():
    current_production = _healthy_stats()
    target_release = _healthy_stats(
        total_occurrence_count=1_050_000,
        distinct_cell_count=10_500,
        distinct_species_count=5_250,
    )

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "pass"
    assert outcome.block_reasons == []
    assert outcome.warn_reasons == []
    assert len(outcome.checks) == 5
    assert all(check["status"] == "ok" for check in outcome.checks)


def test_blocks_when_fact_table_is_missing_a_required_column():
    current_production = _healthy_stats()
    target_release = _healthy_stats(fact_columns=frozenset({"cell", "specieskey"}))

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert any("occurrence_count" in reason for reason in outcome.block_reasons)


def test_blocks_when_dimension_table_is_missing_a_required_column():
    current_production = _healthy_stats()
    target_release = _healthy_stats(dimension_columns=frozenset({"specieskey", "species"}))

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert any("common_name" in reason for reason in outcome.block_reasons)


def test_blocks_when_total_occurrence_count_decreases():
    current_production = _healthy_stats(total_occurrence_count=1_000_000)
    target_release = _healthy_stats(total_occurrence_count=999_999)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert any("occurrence count" in reason for reason in outcome.block_reasons)


def test_blocks_when_distinct_cell_count_decreases():
    current_production = _healthy_stats(distinct_cell_count=10_000)
    target_release = _healthy_stats(distinct_cell_count=9_999)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert any("cell count" in reason for reason in outcome.block_reasons)


def test_blocks_when_distinct_species_count_decreases():
    current_production = _healthy_stats(distinct_species_count=5_000)
    target_release = _healthy_stats(distinct_species_count=4_999)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert any("species count" in reason for reason in outcome.block_reasons)


def test_warns_when_total_occurrence_count_is_essentially_unchanged():
    current_production = _healthy_stats(total_occurrence_count=1_000_000)
    target_release = _healthy_stats(total_occurrence_count=1_000_000)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "warn"
    assert outcome.block_reasons == []
    assert any("occurrence count" in reason for reason in outcome.warn_reasons)


def test_collects_every_block_reason_when_multiple_checks_fail_at_once():
    current_production = _healthy_stats()
    target_release = _healthy_stats(
        fact_columns=frozenset({"cell", "specieskey"}),
        total_occurrence_count=current_production.total_occurrence_count - 1,
        distinct_cell_count=current_production.distinct_cell_count - 1,
    )

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "block"
    assert len(outcome.block_reasons) == 3
    assert any("occurrence_count" in reason for reason in outcome.block_reasons)
    assert any("occurrence count" in reason for reason in outcome.block_reasons)
    assert any("cell count" in reason for reason in outcome.block_reasons)


def test_warns_when_distinct_cell_count_is_essentially_unchanged():
    current_production = _healthy_stats(distinct_cell_count=10_000)
    target_release = _healthy_stats(distinct_cell_count=10_000)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "warn"
    assert any("cell count" in reason for reason in outcome.warn_reasons)


def test_warns_when_distinct_species_count_is_essentially_unchanged():
    current_production = _healthy_stats(distinct_species_count=5_000)
    target_release = _healthy_stats(distinct_species_count=5_000)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "warn"
    assert any("species count" in reason for reason in outcome.warn_reasons)


def test_warns_when_total_occurrence_count_swings_by_more_than_twenty_percent():
    current_production = _healthy_stats(total_occurrence_count=1_000_000)
    target_release = _healthy_stats(total_occurrence_count=1_300_000)

    outcome = evaluate(target_release, current_production)

    assert outcome.verdict == "warn"
    assert outcome.block_reasons == []
    assert any("occurrence count" in reason for reason in outcome.warn_reasons)


def test_every_check_is_reported_even_when_passing():
    current_production = _healthy_stats()
    target_release = _healthy_stats(total_occurrence_count=1_050_000)

    outcome = evaluate(target_release, current_production)

    check_names = {check["check"] for check in outcome.checks}
    assert check_names == {
        "fact schema",
        "dimension schema",
        "total occurrence count",
        "distinct cell count",
        "distinct species count",
    }


def test_compute_stats_from_parquet_derives_schema_and_counts_without_materializing_rows(tmp_path):
    fact_path = str(tmp_path / "fact.parquet")
    dimension_path = str(tmp_path / "dimension.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10},
            {"cell": "u4pruy", "specieskey": "SP2", "occurrence_count": 25},
            {"cell": "u4pruz", "specieskey": "SP1", "occurrence_count": 5},
        ],
        fact_path,
    )
    write_parquet(
        [
            {"specieskey": "SP1", "species": "Pica pica", "common_name": None},
            {"specieskey": "SP2", "species": "Passer domesticus", "common_name": "House Sparrow"},
        ],
        dimension_path,
    )
    con = duckdb.connect()

    stats = compute_stats_from_parquet(con, fact_path, dimension_path)

    assert stats.fact_columns == frozenset({"cell", "specieskey", "occurrence_count"})
    assert stats.dimension_columns == frozenset({"specieskey", "species", "common_name"})
    assert stats.total_occurrence_count == 40
    assert stats.distinct_cell_count == 2
    assert stats.distinct_species_count == 2


def test_log_outcome_logs_a_pass_with_no_reasons(caplog):
    with caplog.at_level("INFO"):
        log_outcome(verdict="pass")

    record = caplog.records[0]
    assert record.message == "monthly_gbif_update_outcome"
    assert record.verdict == "pass"
    assert record.block_reasons == []
    assert record.warn_reasons == []


def test_log_outcome_logs_block_reasons(caplog):
    with caplog.at_level("INFO"):
        log_outcome(verdict="block", block_reasons=["total occurrence count decreased"])

    record = caplog.records[0]
    assert record.verdict == "block"
    assert record.block_reasons == ["total occurrence count decreased"]


def test_log_outcome_logs_warn_reasons(caplog):
    with caplog.at_level("INFO"):
        log_outcome(verdict="warn", warn_reasons=["occurrence count essentially unchanged"])

    record = caplog.records[0]
    assert record.verdict == "warn"
    assert record.warn_reasons == ["occurrence count essentially unchanged"]


# --- Common names cache --------------------------------------------------


def test_new_entrants_returns_specieskeys_not_already_in_the_cache():
    top_n = ["SP1", "SP2", "SP3"]
    existing_cache = {"SP1": "Pica pica"}

    result = new_entrants(top_n, existing_cache)

    assert result == ["SP2", "SP3"]


def test_new_entrants_is_empty_when_every_top_n_specieskey_is_already_cached():
    top_n = ["SP1", "SP2"]
    existing_cache = {"SP1": "Pica pica", "SP2": "House Sparrow"}

    result = new_entrants(top_n, existing_cache)

    assert result == []


def test_merge_into_cache_adds_newly_fetched_entries():
    existing_cache = {"SP1": "Pica pica"}
    newly_fetched = {"SP2": "House Sparrow"}

    result = merge_into_cache(existing_cache, newly_fetched)

    assert result == {"SP1": "Pica pica", "SP2": "House Sparrow"}


def test_merge_into_cache_never_overwrites_an_existing_entry():
    existing_cache = {"SP1": "Pica pica"}
    newly_fetched = {"SP1": "Eurasian Magpie"}

    result = merge_into_cache(existing_cache, newly_fetched)

    assert result == {"SP1": "Pica pica"}


def test_bootstrap_cache_from_production_keeps_only_rows_with_a_common_name(tmp_path):
    dimension_path = str(tmp_path / "dimension.parquet")
    write_parquet(
        [
            {"specieskey": "SP1", "common_name": "Pica pica"},
            {"specieskey": "SP2", "common_name": None},
        ],
        dimension_path,
    )
    con = duckdb.connect()

    result = bootstrap_cache_from_production(con, dimension_path)

    assert result == {"SP1": "Pica pica"}


# --- ChecklistBank fetch --------------------------------------------------


def test_pick_best_name_rejects_a_banding_code_in_favor_of_a_real_name():
    names = ["COST", "American Goldfinch"]

    result = pick_best_name(names)

    assert result == "American Goldfinch"


def test_pick_best_name_rejects_an_informal_lowercase_entry_even_if_longer():
    names = ["pigeon", "Dove"]

    result = pick_best_name(names)

    assert result == "Dove"


def test_pick_best_name_rejects_a_non_ascii_mistagged_name_even_if_longer():
    names = ["Niimíipuu Péekstamit Name", "Red Fox"]

    result = pick_best_name(names)

    assert result == "Red Fox"


def test_pick_best_name_falls_back_to_the_original_list_when_every_candidate_is_junk():
    names = ["COST", "GRTI"]

    result = pick_best_name(names)

    assert result in names


def test_build_common_name_map_dedups_multiple_names_per_taxon():
    records = [
        {"taxonID": "SP1", "name": "COST"},
        {"taxonID": "SP1", "name": "American Goldfinch"},
        {"taxonID": "SP2", "name": "House Sparrow"},
    ]

    result = build_common_name_map(records)

    assert result == {"SP1": "American Goldfinch", "SP2": "House Sparrow"}


def test_fetch_common_names_for_taxa_collects_records_from_every_taxon():
    def fake_fetch(dataset_key, taxon_id):
        return [{"name": f"Name for {taxon_id}"}]

    records = fetch_common_names_for_taxa(315557, ["SP1", "SP2"], fetch_fn=fake_fetch)

    assert sorted(records, key=lambda r: r["taxonID"]) == [
        {"taxonID": "SP1", "name": "Name for SP1"},
        {"taxonID": "SP2", "name": "Name for SP2"},
    ]


# --- BigQuery: generate the target release --------------------------------


def _mock_field(name):
    field = MagicMock()
    field.name = name
    return field


def _mock_bigquery_client():
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
    top_n_job = MagicMock()
    top_n_job.result.return_value = [{"specieskey": "SP1"}, {"specieskey": "SP2"}]

    client = MagicMock()
    client.query.side_effect = [
        scan_job,
        fact_job,
        dimension_job,
        fact_stats_job,
        dimension_stats_job,
        top_n_job,
    ]
    client.extract_table.return_value = extract_job

    fact_table = MagicMock()
    fact_table.schema = [_mock_field("cell"), _mock_field("specieskey"), _mock_field("occurrence_count")]
    dimension_table = MagicMock()
    dimension_table.schema = [_mock_field("specieskey"), _mock_field("species")]
    client.get_table.side_effect = [fact_table, dimension_table]

    return client


def test_generate_target_release_extracts_both_tables_and_returns_their_stats():
    client = _mock_bigquery_client()

    fact_stats, dimension_stats, top_n_specieskeys = generate_target_release(
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
    assert top_n_specieskeys == ["SP1", "SP2"]
    assert client.extract_table.call_count == 2


# --- GCS: local Parquet I/O and promotion ---------------------------------


def test_release_blob_path_builds_a_dated_releases_prefix():
    path = release_blob_path("2026-11-07", "fact_sorted_by_cell.parquet")

    assert path == "releases/2026-11-07/fact_sorted_by_cell.parquet"


def test_write_parquet_writes_rows_that_can_be_read_back(tmp_path):
    path = str(tmp_path / "fact.parquet")
    rows = [
        {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10},
        {"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5},
    ]

    write_parquet(rows, path)

    con = duckdb.connect()
    result = con.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY cell").fetchall()
    assert result == [("u4pruy", "SP1", 10), ("u4pruz", "SP2", 5)]


def test_promote_release_copies_each_dated_object_to_the_bucket_root():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    promote_release(client, "my-bucket", "2026-11-07", ["fact_sorted_by_cell.parquet"])

    source_blob = bucket.blob.return_value
    bucket.blob.assert_called_once_with("releases/2026-11-07/fact_sorted_by_cell.parquet")
    bucket.copy_blob.assert_called_once_with(source_blob, bucket, "fact_sorted_by_cell.parquet")


def test_download_blob_to_file_downloads_the_named_blob():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    download_blob_to_file(client, "my-bucket", "fact_sorted_by_cell.parquet", "/tmp/fact.parquet")

    blob = bucket.blob.return_value
    bucket.blob.assert_called_once_with("fact_sorted_by_cell.parquet")
    blob.download_to_filename.assert_called_once_with("/tmp/fact.parquet")


def test_read_parquet_rows_reads_back_what_write_parquet_wrote(tmp_path):
    path = str(tmp_path / "fact.parquet")
    rows = [{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}]
    write_parquet(rows, path)

    result = read_parquet_rows(path)

    assert result == rows


def test_upload_file_uploads_to_the_given_blob_path():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    upload_file(client, "my-bucket", "common_names_cache.parquet", "/tmp/cache.parquet")

    blob = bucket.blob.return_value
    bucket.blob.assert_called_once_with("common_names_cache.parquet")
    blob.upload_from_filename.assert_called_once_with("/tmp/cache.parquet")


def test_join_common_names_joins_the_cache_onto_the_dimension_rows(tmp_path):
    dimension_path = str(tmp_path / "dimension.parquet")
    output_path = str(tmp_path / "dimension_with_names.parquet")
    write_parquet(
        [
            {"specieskey": "SP1", "species": "Pica pica"},
            {"specieskey": "SP2", "species": "Passer domesticus"},
        ],
        dimension_path,
    )
    cache = {"SP1": "Eurasian Magpie"}

    con = duckdb.connect()
    join_common_names(con, dimension_path, cache, output_path)

    result = read_parquet_rows(output_path)
    assert sorted(result, key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "species": "Pica pica", "common_name": "Eurasian Magpie"},
        {"specieskey": "SP2", "species": "Passer domesticus", "common_name": None},
    ]


# --- Orchestration ---------------------------------------------------------

RAW_DIMENSION_COLUMNS = frozenset(
    {"specieskey", "species", "kingdom", "phylum", "class", "order", "family", "genus"}
)


def _healthy_run_stats(**overrides) -> RunStats:
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
        patch.object(job, "compute_stats_from_parquet", return_value=_healthy_run_stats()),
        patch.object(
            job,
            "generate_target_release",
            return_value=(fact_stats, dimension_stats, ["SP1"]),
        ),
        patch.object(
            job,
            "download_blob_to_file",
            side_effect=[None, None, FileNotFoundError(), None],
        ),
        patch.object(job, "bootstrap_cache_from_production", return_value={}),
        patch.object(job, "fetch_common_names_for_taxa", return_value=[]),
        patch.object(job, "join_common_names") as mock_join,
        patch.object(job, "promote_release") as mock_promote,
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
        patch.object(job, "compute_stats_from_parquet", return_value=_healthy_run_stats()),
        patch.object(
            job,
            "generate_target_release",
            return_value=(fact_stats, dimension_stats, ["SP1"]),
        ),
        patch.object(
            job,
            "download_blob_to_file",
            side_effect=[None, None],
        ),
        patch.object(job, "join_common_names") as mock_join,
        patch.object(job, "promote_release") as mock_promote,
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
    mock_join.assert_not_called()
    mock_promote.assert_not_called()
