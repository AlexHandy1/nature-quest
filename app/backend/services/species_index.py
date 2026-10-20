import duckdb
import pygeohash as pgh

DEFAULT_GEOHASH_PRECISION = 6
CANDIDATE_CELL_STEP_DEG = 0.001

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
            SELECT f.cell, f.occurrence_count, s.species
            FROM read_parquet(?) f
            JOIN read_parquet(?) s ON f.specieskey = s.specieskey
            WHERE f.cell IN (SELECT UNNEST(?)) AND LOWER(s."{column}") = LOWER(?)
        ),
        totals AS (
            SELECT species, SUM(occurrence_count) AS total
            FROM joined
            GROUP BY species
        ),
        ranked_cells AS (
            SELECT species, cell,
                   ROW_NUMBER() OVER (PARTITION BY species ORDER BY occurrence_count DESC) AS rn
            FROM joined
        )
        SELECT t.species, t.total, r.cell AS hotspot_cell
        FROM totals t JOIN ranked_cells r ON t.species = r.species AND r.rn = 1
        ORDER BY t.total DESC
    """
    rows = con.execute(sql, [fact_path, dim_path, cells, taxon_filter["taxonValue"]]).fetchall()

    species_list = []
    for species, total, hotspot_cell in rows:
        lat, lon, lat_err, lon_err = pgh.decode_exactly(hotspot_cell)
        species_list.append(
            {
                "species": species,
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
