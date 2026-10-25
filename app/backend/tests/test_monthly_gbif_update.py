import dataclasses
from unittest.mock import MagicMock, patch

import duckdb
import pytest

import jobs.monthly_gbif_update as job
from jobs.monthly_gbif_update import (
    CACHE_BLOB_PATH,
    DIMENSION_FILENAME,
    EXPECTED_DIMENSION_COLUMNS,
    EXPECTED_FACT_COLUMNS,
    FACT_FILENAME,
    RunStats,
    bootstrap_cache_from_production,
    build_common_name_map,
    compute_stats_from_parquet,
    compute_top_n_specieskeys,
    download_blob_to_file,
    download_blobs_with_prefix,
    evaluate,
    fetch_and_merge_common_names,
    fetch_common_names_for_taxa,
    generate_target_release,
    join_common_names,
    list_blobs_with_prefix,
    log_outcome,
    merge_into_cache,
    new_entrants,
    pick_best_name,
    promote_release,
    read_parquet_rows,
    recombine_ordered_shards,
    release_blob_path,
    run,
    upload_file,
    verify_recombined_row_count,
    verify_sorted_by_cell,
    write_parquet,
)

# --- Guardrails --------------------------------------------------------


def _healthy_stats(**overrides) -> RunStats:
    base = RunStats(
        fact_columns=EXPECTED_FACT_COLUMNS,
        dimension_columns=EXPECTED_DIMENSION_COLUMNS,
        total_occurrence_count=1_000_000,
        distinct_cell_count=10_000,
        distinct_species_count=5_000,
    )
    return dataclasses.replace(base, **overrides)


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


def test_new_entrants_excludes_a_specieskey_cached_as_confirmed_no_name():
    # A cache entry with common_name=None means "already looked up,
    # ChecklistBank has nothing" - must be excluded from new_entrants
    # exactly like a real name would be, not re-fetched forever.
    top_n = ["SP1", "SP2"]
    existing_cache = {"SP1": None}

    result = new_entrants(top_n, existing_cache)

    assert result == ["SP2"]


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


def test_fetch_and_merge_common_names_records_confirmed_empty_entrants_as_none():
    # SP1 gets a real name, SP2's lookup succeeds but returns nothing -
    # both must end up in the merged cache, SP2 explicitly as None, not
    # silently dropped (the bug this function fixes).
    def fake_fetch(dataset_key, taxon_id):
        return [{"name": "Pica pica"}] if taxon_id == "SP1" else []

    result = fetch_and_merge_common_names({}, ["SP1", "SP2"], 315557, fetch_fn=fake_fetch)

    assert result == {"SP1": "Pica pica", "SP2": None}


def test_fetch_and_merge_common_names_keeps_existing_entries_untouched():
    existing_cache = {"SP0": "Existing Name"}

    def fake_fetch(dataset_key, taxon_id):
        return []

    result = fetch_and_merge_common_names(existing_cache, ["SP1"], 315557, fetch_fn=fake_fetch)

    assert result == {"SP0": "Existing Name", "SP1": None}


def test_fetch_and_merge_common_names_returns_existing_cache_unchanged_on_fetch_failure(caplog):
    existing_cache = {"SP0": "Existing Name"}

    def failing_fetch(dataset_key, taxon_id):
        raise ConnectionError("simulated network failure")

    with caplog.at_level("ERROR"):
        result = fetch_and_merge_common_names(existing_cache, ["SP1"], 315557, fetch_fn=failing_fetch)

    assert result == {"SP0": "Existing Name"}
    assert "SP1" not in result


# --- BigQuery: generate the target release --------------------------------


def _mock_bigquery_client():
    scan_job = MagicMock()
    scan_job.result.return_value = None
    scan_job.session_info.session_id = "session-1"

    fact_export_job = MagicMock()
    fact_export_job.result.return_value = None

    dimension_export_job = MagicMock()
    dimension_export_job.result.return_value = None

    client = MagicMock()
    client.query.side_effect = [scan_job, fact_export_job, dimension_export_job]
    return client


def test_generate_target_release_derives_then_export_datas_both_tables_to_gcs():
    client = _mock_bigquery_client()

    generate_target_release(client, "gs://bucket/fact-*.parquet", "gs://bucket/dimension-*.parquet")

    assert client.query.call_count == 3
    fact_export_sql = client.query.call_args_list[1].args[0]
    dimension_export_sql = client.query.call_args_list[2].args[0]
    assert "EXPORT DATA" in fact_export_sql
    assert "gs://bucket/fact-*.parquet" in fact_export_sql
    assert "ORDER BY cell" in fact_export_sql
    assert "EXPORT DATA" in dimension_export_sql
    assert "gs://bucket/dimension-*.parquet" in dimension_export_sql


def test_compute_top_n_specieskeys_selects_the_smallest_set_covering_the_threshold(tmp_path):
    fact_path = str(tmp_path / "fact.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 97},
            {"cell": "u4pruy", "specieskey": "SP2", "occurrence_count": 2},
            {"cell": "u4pruy", "specieskey": "SP3", "occurrence_count": 1},
        ],
        fact_path,
    )
    con = duckdb.connect()

    result = compute_top_n_specieskeys(con, fact_path, coverage=0.99)

    assert result == ["SP1", "SP2"]


# --- GCS: sharded fact export recombine -------------------------------------


def test_list_blobs_with_prefix_returns_matching_blob_names():
    blob1, blob2 = MagicMock(), MagicMock()
    blob1.name = "releases/2026-11-07/fact_sorted_by_cell-0.parquet"
    blob2.name = "releases/2026-11-07/fact_sorted_by_cell-1.parquet"
    bucket = MagicMock()
    bucket.list_blobs.return_value = [blob1, blob2]
    client = MagicMock()
    client.bucket.return_value = bucket

    result = list_blobs_with_prefix(client, "my-bucket", "releases/2026-11-07/fact_sorted_by_cell-")

    bucket.list_blobs.assert_called_once_with(prefix="releases/2026-11-07/fact_sorted_by_cell-")
    assert result == [blob1.name, blob2.name]


def test_download_blobs_with_prefix_downloads_every_match_and_returns_ordered_local_paths(tmp_path):
    blob1, blob2 = MagicMock(), MagicMock()
    blob1.name = "releases/2026-11-07/fact_sorted_by_cell-0.parquet"
    blob2.name = "releases/2026-11-07/fact_sorted_by_cell-1.parquet"
    bucket = MagicMock()
    bucket.list_blobs.return_value = [blob1, blob2]
    client = MagicMock()
    client.bucket.return_value = bucket
    local_dir = str(tmp_path / "shards")

    result = download_blobs_with_prefix(
        client, "my-bucket", "releases/2026-11-07/fact_sorted_by_cell-", local_dir
    )

    assert result == [
        f"{local_dir}/fact_sorted_by_cell-0.parquet",
        f"{local_dir}/fact_sorted_by_cell-1.parquet",
    ]
    assert bucket.blob.return_value.download_to_filename.call_count == 2
    bucket.blob.return_value.download_to_filename.assert_any_call(f"{local_dir}/fact_sorted_by_cell-0.parquet")
    bucket.blob.return_value.download_to_filename.assert_any_call(f"{local_dir}/fact_sorted_by_cell-1.parquet")


def test_recombine_ordered_shards_concatenates_in_the_given_order_without_resorting(tmp_path):
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    shard0 = str(shard_dir / "shard0.parquet")
    shard1 = str(shard_dir / "shard1.parquet")
    write_parquet(
        [{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}],
        shard0,
    )
    write_parquet(
        [{"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5}],
        shard1,
    )
    output_path = str(tmp_path / "recombined.parquet")
    con = duckdb.connect()

    recombine_ordered_shards(con, [shard0, shard1], output_path)

    result = read_parquet_rows(output_path)
    assert result == [
        {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10},
        {"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5},
    ]


def test_recombine_ordered_shards_preserves_shard_order_even_when_each_shard_is_locally_unsorted(tmp_path):
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    shard0 = str(shard_dir / "shard0.parquet")
    shard1 = str(shard_dir / "shard1.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10},
            {"cell": "u4prux", "specieskey": "SP3", "occurrence_count": 1},
        ],
        shard0,
    )
    write_parquet(
        [{"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5}],
        shard1,
    )
    output_path = str(tmp_path / "recombined.parquet")
    con = duckdb.connect()

    recombine_ordered_shards(con, [shard0, shard1], output_path)

    result = read_parquet_rows(output_path)
    assert [row["cell"] for row in result] == ["u4pruy", "u4prux", "u4pruz"]


def test_recombine_ordered_shards_follows_the_given_list_order_not_filename_order(tmp_path):
    # Isolates a real DuckDB assumption: read_parquet([paths]) must
    # respect the explicit list order we pass (from GCS's guaranteed
    # lexicographic blob listing), not silently re-sort by filename
    # internally. Deliberately names the file with the lower cell value
    # so it sorts AFTER the other alphabetically, to catch DuckDB
    # reordering by filename rather than honouring the given list.
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    path_z = str(shard_dir / "z_shard.parquet")
    path_a = str(shard_dir / "a_shard.parquet")
    write_parquet([{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}], path_z)
    write_parquet([{"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5}], path_a)
    output_path = str(tmp_path / "recombined.parquet")
    con = duckdb.connect()

    recombine_ordered_shards(con, [path_z, path_a], output_path)

    result = read_parquet_rows(output_path)
    assert [row["cell"] for row in result] == ["u4pruy", "u4pruz"]


def test_verify_recombined_row_count_raises_on_mismatch(tmp_path):
    shard_path = str(tmp_path / "shard.parquet")
    output_path = str(tmp_path / "recombined.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10},
            {"cell": "u4pruz", "specieskey": "SP2", "occurrence_count": 5},
        ],
        shard_path,
    )
    write_parquet([{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}], output_path)
    con = duckdb.connect()

    with pytest.raises(ValueError, match="mismatch"):
        verify_recombined_row_count(con, [shard_path], output_path)


def test_verify_recombined_row_count_passes_silently_when_counts_match(tmp_path):
    path = str(tmp_path / "fact.parquet")
    write_parquet([{"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 10}], path)
    con = duckdb.connect()

    verify_recombined_row_count(con, [path], path)


def test_verify_sorted_by_cell_raises_when_row_groups_are_out_of_order(tmp_path):
    # ROW_GROUP_SIZE only takes effect with enough rows to actually split
    # (DuckDB won't honour it for a handful of rows) - 2048-row blocks
    # per group, in reverse cell order, reliably produces two real row
    # groups whose min/max ranges violate ascending order.
    path = str(tmp_path / "fact.parquet")
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT 'u4pruz' AS cell, 'SP' || i AS specieskey, i AS occurrence_count FROM range(2048) t(i)
            UNION ALL
            SELECT 'u4pruy', 'SP' || i, i FROM range(2048) t(i)
        ) TO '{path}' (FORMAT PARQUET, ROW_GROUP_SIZE 2048)
    """)

    with pytest.raises(ValueError, match="not sorted"):
        verify_sorted_by_cell(con, path)


def test_verify_sorted_by_cell_passes_silently_when_already_sorted(tmp_path):
    path = str(tmp_path / "fact.parquet")
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT 'u4pruy' AS cell, 'SP' || i AS specieskey, i AS occurrence_count FROM range(2048) t(i)
            UNION ALL
            SELECT 'u4pruz', 'SP' || i, i FROM range(2048) t(i)
        ) TO '{path}' (FORMAT PARQUET, ROW_GROUP_SIZE 2048)
    """)

    verify_sorted_by_cell(con, path)


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
    base = RunStats(
        fact_columns=frozenset({"cell", "specieskey", "occurrence_count"}),
        dimension_columns=RAW_DIMENSION_COLUMNS | {"common_name"},
        total_occurrence_count=1_000_000,
        distinct_cell_count=10_000,
        distinct_species_count=5_000,
    )
    return dataclasses.replace(base, **overrides)


def test_run_promotes_the_release_on_a_clean_pass(tmp_path):
    bigquery_client = MagicMock()
    storage_client = MagicMock()
    con = MagicMock()

    current_production_stats = _healthy_run_stats()
    target_stats = _healthy_run_stats(
        total_occurrence_count=1_050_000, distinct_cell_count=10_500, distinct_species_count=5_250
    )

    def fake_fetch(dataset_key, taxon_id):
        return [{"name": "Pica pica"}] if taxon_id == "SP1" else []

    with (
        patch.object(job, "generate_target_release") as mock_generate,
        patch.object(
            job, "download_blobs_with_prefix", return_value=[f"{tmp_path}/shards/shard0.parquet"]
        ) as mock_download_shards,
        patch.object(job, "recombine_ordered_shards") as mock_recombine,
        patch.object(job, "verify_recombined_row_count") as mock_verify_count,
        patch.object(job, "verify_sorted_by_cell") as mock_verify_sorted,
        patch.object(
            job,
            "download_blob_to_file",
            side_effect=[None, None, FileNotFoundError()],
        ),
        patch.object(job, "compute_stats_from_parquet", side_effect=[current_production_stats, target_stats]),
        patch.object(job, "compute_top_n_specieskeys", return_value=["SP1", "SP2"]),
        patch.object(job, "bootstrap_cache_from_production", return_value={}),
        patch.object(job, "join_common_names") as mock_join,
        patch.object(job, "upload_file") as mock_upload,
        patch.object(job, "promote_release") as mock_promote,
    ):
        outcome = run(
            bigquery_client,
            storage_client,
            con,
            "my-bucket",
            workdir=str(tmp_path),
            release_date="2026-11-07",
            fetch_fn=fake_fetch,
        )

    assert outcome.verdict == "pass"
    mock_generate.assert_called_once()
    assert mock_download_shards.call_count == 2
    assert mock_recombine.call_count == 2
    assert mock_verify_count.call_count == 2
    mock_verify_sorted.assert_called_once()
    mock_join.assert_called_once()
    # fact + dimension uploads, plus the cache upload now that a
    # genuinely new entrant (SP1, real name) and a confirmed-empty one
    # (SP2, no name) both need recording
    assert mock_upload.call_count == 3
    cache_upload_call = next(call for call in mock_upload.call_args_list if call.args[2] == CACHE_BLOB_PATH)
    uploaded_cache_path = cache_upload_call.args[3]
    assert sorted(read_parquet_rows(uploaded_cache_path), key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": None},
    ]
    joined_cache_arg = mock_join.call_args.args[2]
    assert joined_cache_arg == {"SP1": "Pica pica", "SP2": None}
    mock_promote.assert_called_once_with(
        storage_client, "my-bucket", "2026-11-07", [FACT_FILENAME, DIMENSION_FILENAME]
    )


def test_run_does_not_promote_when_guardrails_block(tmp_path):
    bigquery_client = MagicMock()
    storage_client = MagicMock()
    con = MagicMock()

    current_production_stats = _healthy_run_stats()
    target_stats = _healthy_run_stats(total_occurrence_count=500_000)

    with (
        patch.object(job, "generate_target_release"),
        patch.object(job, "download_blobs_with_prefix", return_value=[f"{tmp_path}/shards/shard0.parquet"]),
        patch.object(job, "recombine_ordered_shards"),
        patch.object(job, "verify_recombined_row_count"),
        patch.object(job, "verify_sorted_by_cell"),
        patch.object(job, "download_blob_to_file", side_effect=[None, None]),
        patch.object(job, "compute_stats_from_parquet", side_effect=[current_production_stats, target_stats]),
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
