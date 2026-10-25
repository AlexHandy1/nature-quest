import os
import shutil
from unittest.mock import MagicMock, patch

import jobs.backfill_common_names_negative_cache as backfill_module
from jobs.backfill_common_names_negative_cache import backfill_negative_cache
from jobs.monthly_gbif_update import FACT_FILENAME, read_parquet_rows, write_parquet


def test_backfill_negative_cache_marks_missing_top_n_entries_as_confirmed_empty(tmp_path):
    fact_path = str(tmp_path / "fixture_fact.parquet")
    cache_path = str(tmp_path / "fixture_cache.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 97},
            {"cell": "u4pruy", "specieskey": "SP2", "occurrence_count": 2},
            {"cell": "u4pruy", "specieskey": "SP3", "occurrence_count": 1},
        ],
        fact_path,
    )
    write_parquet([{"specieskey": "SP1", "common_name": "Pica pica"}], cache_path)

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    uploaded = {}

    def fake_download(client, bucket_name, blob_path, local_path):
        source = fact_path if blob_path == FACT_FILENAME else cache_path
        shutil.copy(source, local_path)

    def fake_upload(client, bucket_name, blob_path, local_path):
        uploaded["rows"] = read_parquet_rows(local_path)

    with (
        patch.object(backfill_module, "download_blob_to_file", side_effect=fake_download),
        patch.object(backfill_module, "upload_file", side_effect=fake_upload),
    ):
        summary = backfill_negative_cache(MagicMock(), "my-bucket", workdir)

    assert summary == {
        "top_n_count": 2,
        "prior_hits": 1,
        "stale_cache_entries_outside_top_n": 0,
        "backfilled_as_confirmed_empty": 1,
    }
    assert sorted(uploaded["rows"], key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": None},
    ]


def test_backfill_negative_cache_keeps_a_cache_entry_that_has_dropped_out_of_top_n(tmp_path):
    # A species can be cached in a prior run and then drop out of this
    # run's top_n if its occurrence rank shifted slightly - cache isn't
    # a strict subset of top_n, and that entry should be kept as-is,
    # not touched or dropped.
    fact_path = str(tmp_path / "fixture_fact.parquet")
    cache_path = str(tmp_path / "fixture_cache.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 97},
            {"cell": "u4pruy", "specieskey": "SP2", "occurrence_count": 2},
            {"cell": "u4pruy", "specieskey": "SP3", "occurrence_count": 1},
        ],
        fact_path,
    )
    write_parquet(
        [
            {"specieskey": "SP1", "common_name": "Pica pica"},
            {"specieskey": "SP_RETIRED", "common_name": "Dropped Out Species"},
        ],
        cache_path,
    )

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    uploaded = {}

    def fake_download(client, bucket_name, blob_path, local_path):
        source = fact_path if blob_path == FACT_FILENAME else cache_path
        shutil.copy(source, local_path)

    def fake_upload(client, bucket_name, blob_path, local_path):
        uploaded["rows"] = read_parquet_rows(local_path)

    with (
        patch.object(backfill_module, "download_blob_to_file", side_effect=fake_download),
        patch.object(backfill_module, "upload_file", side_effect=fake_upload),
    ):
        summary = backfill_negative_cache(MagicMock(), "my-bucket", workdir)

    assert summary["stale_cache_entries_outside_top_n"] == 1
    assert summary["backfilled_as_confirmed_empty"] == 1
    assert sorted(uploaded["rows"], key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": None},
        {"specieskey": "SP_RETIRED", "common_name": "Dropped Out Species"},
    ]


def test_backfill_negative_cache_is_a_noop_when_cache_already_covers_top_n(tmp_path):
    fact_path = str(tmp_path / "fixture_fact.parquet")
    cache_path = str(tmp_path / "fixture_cache.parquet")
    write_parquet(
        [
            {"cell": "u4pruy", "specieskey": "SP1", "occurrence_count": 97},
            {"cell": "u4pruy", "specieskey": "SP2", "occurrence_count": 2},
            {"cell": "u4pruy", "specieskey": "SP3", "occurrence_count": 1},
        ],
        fact_path,
    )
    write_parquet(
        [
            {"specieskey": "SP1", "common_name": "Pica pica"},
            {"specieskey": "SP2", "common_name": None},
        ],
        cache_path,
    )

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    uploaded = {}

    def fake_download(client, bucket_name, blob_path, local_path):
        source = fact_path if blob_path == FACT_FILENAME else cache_path
        shutil.copy(source, local_path)

    def fake_upload(client, bucket_name, blob_path, local_path):
        uploaded["rows"] = read_parquet_rows(local_path)

    with (
        patch.object(backfill_module, "download_blob_to_file", side_effect=fake_download),
        patch.object(backfill_module, "upload_file", side_effect=fake_upload),
    ):
        summary = backfill_negative_cache(MagicMock(), "my-bucket", workdir)

    assert summary["backfilled_as_confirmed_empty"] == 0
    assert sorted(uploaded["rows"], key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": None},
    ]
