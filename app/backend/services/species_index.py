import os

import duckdb
import pygeohash as pgh

from services.logging_client import timed_checkpoint
from services.secret_manager_client import fetch_secret

DEFAULT_GEOHASH_PRECISION = 6
CANDIDATE_CELL_STEP_DEG = 0.001
GCS_HMAC_KEY_ID_SECRET = "gcs-index-data-hmac-key-id"
GCS_HMAC_SECRET_SECRET = "gcs-index-data-hmac-secret"
TOP_SPECIES_COUNT = 5

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
    with timed_checkpoint("t_connect"):
        con = duckdb.connect()
    if os.environ.get("K_SERVICE"):
        with timed_checkpoint("t_install_load_httpfs"):
            con.execute("INSTALL httpfs")
            con.execute("LOAD httpfs")
        with timed_checkpoint("t_fetch_secret_gcs_key_id"):
            key_id = fetch_secret(GCS_HMAC_KEY_ID_SECRET)
        with timed_checkpoint("t_fetch_secret_gcs_secret"):
            secret = fetch_secret(GCS_HMAC_SECRET_SECRET)
        with timed_checkpoint("t_create_secret"):
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
            SELECT species, cell, occurrence_count,
                   ROW_NUMBER() OVER (PARTITION BY species ORDER BY occurrence_count DESC) AS rn
            FROM joined
        )
        SELECT t.species, t.common_name, t.total, r.cell AS hotspot_cell, r.occurrence_count AS hotspot_count
        FROM totals t JOIN ranked_cells r ON t.species = r.species AND r.rn = 1
        ORDER BY t.total DESC
    """
    with timed_checkpoint(
        "t_query_execute",
        taxon_rank=taxon_filter["taxonRank"],
        taxon_value=taxon_filter["taxonValue"],
    ):
        rows = con.execute(sql, [fact_path, dim_path, cells, taxon_filter["taxonValue"]]).fetchall()

    species_list = []
    for species, common_name, total, hotspot_cell, hotspot_count in rows:
        lat, lon, lat_err, lon_err = pgh.decode_exactly(hotspot_cell)
        species_list.append(
            {
                "species": species,
                "common_name": common_name,
                "count": int(total),
                "hotspot_count": int(hotspot_count),
                "cell": hotspot_cell,
                "hotspot_lat": lat,
                "hotspot_lon": lon,
                "cell_lat_err": lat_err,
                "cell_lon_err": lon_err,
            }
        )
    return species_list


def parse_polygon_vertices(polygon_wkt: str) -> list[tuple[float, float]]:
    """Parses a WKT POLYGON's vertices into (lon, lat) pairs, excluding the
    closing repeat of the first vertex."""
    ring = polygon_wkt.removeprefix("POLYGON((").removesuffix("))")
    points = [_parse_vertex(pair) for pair in ring.split(",")]
    if points[0] == points[-1]:
        points = points[:-1]
    return points


def _parse_vertex(pair: str) -> tuple[float, float]:
    lon, lat = pair.split()
    return float(lon), float(lat)


def polygon_centroid(polygon_wkt: str) -> tuple[float, float]:
    """Simple average of a WKT POLYGON's vertices ("lon lat" pairs), excluding
    the closing repeat of the first vertex. Returns (lat, lon)."""
    points = parse_polygon_vertices(polygon_wkt)
    lats = [lat for _, lat in points]
    lons = [lon for lon, _ in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _select_species_across_groups(groups: list[list[dict]], target_total: int) -> list[dict]:
    num_groups = len(groups)
    base_quota, remainder = divmod(target_total, num_groups)
    quotas = [base_quota + (1 if i < remainder else 0) for i in range(num_groups)]

    taken = [group[:quota] for group, quota in zip(groups, quotas)]
    shortfall = target_total - sum(len(t) for t in taken)

    while shortfall > 0:
        gave_any = False
        for i, group in enumerate(groups):
            if shortfall == 0:
                break
            available_extra = group[len(taken[i]) : len(taken[i]) + 1]
            if available_extra:
                taken[i].extend(available_extra)
                shortfall -= 1
                gave_any = True
        if not gave_any:
            break

    selected = []
    for group_taken in taken:
        selected.extend(group_taken)
    return sorted(selected, key=lambda s: s["count"], reverse=True)


def _polygon_bbox(polygon_wkt: str) -> tuple[float, float, float, float]:
    points = parse_polygon_vertices(polygon_wkt)
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), max(lats), min(lons), max(lons)
