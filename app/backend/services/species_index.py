import pygeohash as pgh

DEFAULT_GEOHASH_PRECISION = 6
CANDIDATE_CELL_STEP_DEG = 0.001


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


def _polygon_bbox(polygon_wkt: str) -> tuple[float, float, float, float]:
    coords_str = polygon_wkt.split("((")[1].split("))")[0]
    points = [tuple(map(float, pair.split())) for pair in coords_str.split(",")]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lats), max(lats), min(lons), max(lons)
