from unittest.mock import MagicMock

from jobs.wikipedia_upload_embeddings_table import (
    WIKIPEDIA_EMBEDDINGS_FILENAME,
    run,
    upload_file,
)


def test_upload_file_uploads_to_the_given_blob_path():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    upload_file(client, "my-bucket", "wikipedia_species_embeddings.parquet", "/tmp/table.parquet")

    blob = bucket.blob.return_value
    bucket.blob.assert_called_once_with("wikipedia_species_embeddings.parquet")
    blob.upload_from_filename.assert_called_once_with("/tmp/table.parquet")


def test_run_uploads_the_local_table_to_the_fixed_bucket_root_filename():
    bucket = MagicMock()
    client = MagicMock()
    client.bucket.return_value = bucket

    run(client, "my-bucket", "/tmp/wikipedia_species_embeddings.parquet")

    bucket.blob.assert_called_once_with(WIKIPEDIA_EMBEDDINGS_FILENAME)
    bucket.blob.return_value.upload_from_filename.assert_called_once_with(
        "/tmp/wikipedia_species_embeddings.parquet"
    )
