"""Manual-trigger upload step of the Wikipedia-embeddings pipeline (fetch ->
embed -> build table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md).
Uploads the build step's finished local parquet to the production GCS bucket
at a fixed root filename, the same layout convention as the fact/dimension
tables (jobs/monthly_gbif_update.py's FACT_FILENAME/DIMENSION_FILENAME).
No Terraform/Cloud Run Job wrapper yet - run manually, deliberately deferred
to a later slice."""

import logging
import os

from google.cloud import storage  # type: ignore[attr-defined,import-untyped]

logger = logging.getLogger("wikipedia_upload_embeddings_table")

WIKIPEDIA_EMBEDDINGS_FILENAME = "wikipedia_species_embeddings.parquet"


def upload_file(client: storage.Client, bucket_name: str, blob_path: str, local_path: str) -> None:
    client.bucket(bucket_name).blob(blob_path).upload_from_filename(local_path)


def run(client: storage.Client, bucket_name: str, local_path: str) -> None:
    upload_file(client, bucket_name, WIKIPEDIA_EMBEDDINGS_FILENAME, local_path)
    logger.info(
        "wikipedia_upload_embeddings_table_complete",
        extra={"bucket_name": bucket_name, "blob_path": WIKIPEDIA_EMBEDDINGS_FILENAME},
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bucket_name = os.environ["INDEX_DATA_BUCKET"]
    local_path = os.environ["WIKIPEDIA_EMBEDDINGS_TABLE_OUTPUT_PATH"]
    run(storage.Client(), bucket_name, local_path)


if __name__ == "__main__":
    main()
