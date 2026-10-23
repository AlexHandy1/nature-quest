from unittest.mock import MagicMock

import duckdb

from jobs.monthly_gbif_update_gcs_release import (
    download_blob_to_file,
    promote_release,
    read_parquet_rows,
    release_blob_path,
    upload_release_file,
    write_parquet,
)


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


def test_upload_release_file_uploads_to_the_dated_releases_path():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    upload_release_file(client, "my-bucket", "2026-11-07", "/tmp/fact.parquet", "fact_sorted_by_cell.parquet")

    blob = bucket.blob.return_value
    bucket.blob.assert_called_once_with("releases/2026-11-07/fact_sorted_by_cell.parquet")
    blob.upload_from_filename.assert_called_once_with("/tmp/fact.parquet")


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
