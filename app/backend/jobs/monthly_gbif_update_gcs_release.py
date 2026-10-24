import duckdb
import pandas as pd
from google.cloud import storage  # type: ignore[attr-defined]


def release_blob_path(release_date: str, filename: str) -> str:
    return f"releases/{release_date}/{filename}"


def write_parquet(rows: list[dict], path: str) -> None:
    con = duckdb.connect()
    con.register("rows_df", pd.DataFrame(rows))
    con.execute(f"COPY rows_df TO '{path}' (FORMAT PARQUET)")


def join_common_names(
    con: duckdb.DuckDBPyConnection, dimension_path: str, cache: dict[str, str], output_path: str
) -> None:
    """Joins the common-names cache onto the dimension table entirely
    within DuckDB. dimension_path/output_path are local paths - the raw
    dimension file is downloaded locally first (download_blob_to_file)
    and the joined result is uploaded afterward (upload_file), so this
    never needs httpfs/GCS credentials of its own; dimension rows never
    pass through Python regardless of table size."""
    cache_df = pd.DataFrame({"specieskey": list(cache.keys()), "common_name": list(cache.values())})
    con.register("cache_df", cache_df)
    con.execute(f"""
        COPY (
            SELECT d.*, c.common_name
            FROM read_parquet('{dimension_path}') d
            LEFT JOIN cache_df c ON d.specieskey = c.specieskey
        ) TO '{output_path}' (FORMAT PARQUET)
    """)


def download_blob_to_file(
    client: storage.Client, bucket_name: str, blob_path: str, local_path: str
) -> None:
    bucket = client.bucket(bucket_name)
    bucket.blob(blob_path).download_to_filename(local_path)


def upload_file(client: storage.Client, bucket_name: str, blob_path: str, local_path: str) -> None:
    bucket = client.bucket(bucket_name)
    bucket.blob(blob_path).upload_from_filename(local_path)


def read_parquet_rows(path: str) -> list[dict]:
    con = duckdb.connect()
    columns = [col[0] for col in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    rows = con.execute(f"SELECT * FROM read_parquet('{path}')").fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


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
