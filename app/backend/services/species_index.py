import os

import duckdb
import pygeohash as pgh

from services.secret_manager_client import fetch_secret

DEFAULT_GEOHASH_PRECISION = 6
CANDIDATE_CELL_STEP_DEG = 0.001
GCS_HMAC_KEY_ID_SECRET = "gcs-index-data-hmac-key-id"
GCS_HMAC_SECRET_SECRET = "gcs-index-data-hmac-secret"

RANK_TO_COLUMN = {
    "kingdom": "kingdom",
    "phylum": "phylum",
    "class": "class",
    "order": "order",
    "family": "family",
    "genus": "genus",
}


def candidate_cells(
    polygon_wkt: str,
    precision: int = DEFAULT_GEOHASH_PRECISION,
    step_deg: float = CANDIDATE_CELL_STEP_DEG,
) -> list[str]:
    min_lat, max_lat, min_lon, max_lon = _polygon_bbox(polygon_wkt)
    cells = set()
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            cells.add(pgh.encode(lat, lon, precision=precision))
            lon += step_deg
        lat += step_deg
    return sorted(cells)


def open_index_connection() -> duckdb.DuckDBPyConnection:
    """On Cloud Run (K_SERVICE set), configures httpfs with a long-lived
    HMAC key for the read-only app identity (provisioned in
    infra/gcs_index_data.tf) so read_parquet can address gs:// paths.
    Locally, returns a plain connection - callers pass local file paths
    instead."""
    con = duckdb.connect()
    if os.environ.get("K_SERVICE"):
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        key_id = fetch_secret(GCS_HMAC_KEY_ID_SECRET)
        secret = fetch_secret(GCS_HMAC_SECRET_SECRET)
        con.execute(f"CREATE SECRET (TYPE GCS, KEY_ID '{key_id}', SECRET '{secret}')")
    return con


def query_species_for_filter(
    con: duckdb.DuckDBPyConnection,
    fact_path: str,
    dim_path: str,
    taxon_filter: dict,
    cells: list[str],
) -> list[dict]:
    column = RANK_TO_COLUMN[taxon_filter["taxonRank"]]
    sql = f"""
        WITH joined AS (
            SELECT f.cell, f.occurrence_count, s.species, s.common_name
            FROM read_parquet(?) f
            JOIN read_parquet(?) s ON f.specieskey = s.specieskey
            WHERE f.cell IN (SELECT UNNEST(?)) AND LOWER(s."{column}") = LOWER(?)
        ),
        totals AS (
            SELECT species, common_name, SUM(occurrence_count) AS total
            FROM joined
            GROUP BY species, common_name
        ),
        ranked_cells AS (
            SELECT species, cell,
                   ROW_NUMBER() OVER (PARTITION BY species ORDER BY occurrence_count DESC) AS rn
            FROM joined
        )
        SELECT t.species, t.common_name, t.total, r.cell AS hotspot_cell
        FROM totals t JOIN ranked_cells r ON t.species = r.species AND r.rn = 1
        ORDER BY t.total DESC
    """
    rows = con.execute(sql, [fact_path, dim_path, cells, taxon_filter["taxonValue"]]).fetchall()

    species_list = []
    for species, common_name, total, hotspot_cell in rows:
        lat, lon, lat_err, lon_err = pgh.decode_exactly(hotspot_cell)
        species_list.append(
            {
                "species": species,
                "common_name": common_name,
                "count": int(total),
                "cell": hotspot_cell,
                "hotspot_lat": lat,
                "hotspot_lon": lon,
                "cell_lat_err": lat_err,
                "cell_lon_err": lon_err,
            }
        )
    return species_list


def _polygon_bbox(polygon_wkt: str) -> tuple[float, float, float, float]:
    coords_str = polygon_wkt.split("((")[1].split("))")[0]
    points = [tuple(map(float, pair.split())) for pair in coords_str.split(",")]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), max(lats), min(lons), max(lons)
