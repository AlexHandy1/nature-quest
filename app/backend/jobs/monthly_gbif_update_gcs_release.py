import duckdb
import pandas as pd
from google.cloud import storage  # type: ignore[attr-defined]


def release_blob_path(release_date: str, filename: str) -> str:
    return f"releases/{release_date}/{filename}"


def write_parquet(rows: list[dict], path: str) -> None:
    con = duckdb.connect()
    con.register("rows_df", pd.DataFrame(rows))
    con.execute(f"COPY rows_df TO '{path}' (FORMAT PARQUET)")


def upload_release_file(
    client: storage.Client, bucket_name: str, release_date: str, local_path: str, filename: str
) -> None:
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(release_blob_path(release_date, filename))
    blob.upload_from_filename(local_path)


def promote_release(
    client: storage.Client, bucket_name: str, release_date: str, filenames: list[str]
) -> None:
    """Copies each dated release object onto the fixed root path the live
    app reads from (same filename, bucket root) - the one action that
    actually makes a new release live."""
    bucket = client.bucket(bucket_name)
    for filename in filenames:
        source_blob = bucket.blob(release_blob_path(release_date, filename))
        bucket.copy_blob(source_blob, bucket, filename)
