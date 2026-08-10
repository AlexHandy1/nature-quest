import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import httpx

GBIF_OCCURRENCE_SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
# Fixed boundary for Retiro Park, Madrid — this slice's only supported area.
GBIF_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)
YEAR_RANGE = "2023,2026"
FALLBACK_YEAR = "2026"
SCALE_GUARD_THRESHOLD = 1000
MAX_RETRIES = 2
REQUEST_TIMEOUT = 30.0
TOP_SPECIES_COUNT = 5
MAX_CONCURRENT_GBIF_REQUESTS = 3
DEFAULT_GRID_N = 5
MIN_POINTS_TO_CLUSTER = 3  # <=2 points: clustering is meaningless, fall back to plain average

KEY_PARAM_BY_RANK = {
    "kingdom": "kingdomKey",
    "phylum": "phylumKey",
    "class": "classKey",
    "order": "orderKey",
    "family": "familyKey",
    "genus": "genusKey",
}


class GbifUnavailableError(Exception):
    """Raised when GBIF occurrence/search fails after all retries."""


def polygon_centroid(polygon_wkt: str) -> tuple[float, float]:
    """Simple average of a WKT POLYGON's vertices ("lon lat" pairs), excluding
    the closing repeat of the first vertex. Returns (lat, lon)."""
    ring = polygon_wkt.removeprefix("POLYGON((").removesuffix("))")
    points = [tuple(map(float, pair.split())) for pair in ring.split(",")]
    if points[0] == points[-1]:
        points = points[:-1]
    lats = [lat for _, lat in points]
    lons = [lon for lon, _ in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def fetch_top_species(taxon_filters: list[dict], polygon: str = GBIF_POLYGON) -> list[dict]:
    def _fetch_and_rank(f: dict) -> list[dict]:
        return _rank_top_species(
            _fetch_occurrences({KEY_PARAM_BY_RANK[f["taxon_rank"]]: f["taxon_key"]}, polygon)
        )

    max_workers = min(len(taxon_filters), MAX_CONCURRENT_GBIF_REQUESTS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        groups = list(executor.map(_fetch_and_rank, taxon_filters))
    return _select_species_across_groups(groups, TOP_SPECIES_COUNT)


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


def _fetch_occurrences(extra_params: dict, polygon: str) -> list[dict]:
    probe = _gbif_search({**_base_params(YEAR_RANGE, polygon), "limit": 0, **extra_params})
    year = FALLBACK_YEAR if probe.get("count", 0) > SCALE_GUARD_THRESHOLD else YEAR_RANGE

    results = []
    offset = 0
    while True:
        data = _gbif_search(
            {**_base_params(year, polygon), "limit": 300, "offset": offset, **extra_params}
        )
        page = data.get("results", [])
        results.extend(page)
        if data.get("endOfRecords", True):
            break
        offset += 300
    return results


def _base_params(year: str, polygon: str) -> dict:
    return {
        "geometry": polygon,
        "year": year,
        "hasCoordinate": "true",
        "occurrenceStatus": "PRESENT",
    }


def _gbif_search(params: dict) -> dict:
    for _ in range(MAX_RETRIES + 1):
        data = _request_occurrence_page(params)
        if data is not None:
            return data
    raise GbifUnavailableError("GBIF occurrence/search failed after retries")


def _request_occurrence_page(params: dict) -> dict | None:
    try:
        response = httpx.get(GBIF_OCCURRENCE_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    return data if isinstance(data, dict) else None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cluster_species_hotspot(points: list[tuple[float, float]], grid_n: int = DEFAULT_GRID_N) -> dict:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    avg_lat, avg_lon = sum(lats) / len(lats), sum(lons) / len(lons)

    if len(points) < MIN_POINTS_TO_CLUSTER:
        return {
            "cluster_lat": avg_lat,
            "cluster_lon": avg_lon,
            "cells_occupied": None,
            "winning_cell_count": None,
            "fallback_reason": "too_few_points",
            "distance_from_average_m": 0.0,
        }

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_step = (max_lat - min_lat) / grid_n if max_lat > min_lat else None
    lon_step = (max_lon - min_lon) / grid_n if max_lon > min_lon else None

    cells = defaultdict(list)
    for lat, lon in points:
        row = min(int((lat - min_lat) / lat_step), grid_n - 1) if lat_step else 0
        col = min(int((lon - min_lon) / lon_step), grid_n - 1) if lon_step else 0
        cells[(row, col)].append((lat, lon))

    _, winning_points = max(cells.items(), key=lambda kv: len(kv[1]))
    cluster_lat = sum(p[0] for p in winning_points) / len(winning_points)
    cluster_lon = sum(p[1] for p in winning_points) / len(winning_points)

    return {
        "cluster_lat": cluster_lat,
        "cluster_lon": cluster_lon,
        "cells_occupied": len(cells),
        "winning_cell_count": len(winning_points),
        "fallback_reason": None,
        "distance_from_average_m": _haversine_m(avg_lat, avg_lon, cluster_lat, cluster_lon),
    }


def _rank_top_species(occurrences: list[dict]) -> list[dict]:
    by_species = defaultdict(list)
    for occ in occurrences:
        species = occ.get("species") or occ.get("scientificName")
        if species:
            by_species[species].append(occ)

    ranked = sorted(by_species.items(), key=lambda item: len(item[1]), reverse=True)

    species_list = []
    for species, records in ranked:
        points = [
            (r["decimalLatitude"], r["decimalLongitude"])
            for r in records
            if r.get("decimalLatitude") is not None and r.get("decimalLongitude") is not None
        ]
        if not points:
            continue
        clustering = _cluster_species_hotspot(points)
        species_list.append(
            {
                "species": species,
                "species_key": records[0].get("speciesKey"),
                "count": len(records),
                "kingdom": records[0].get("kingdom", "?"),
                "hotspot_lat": clustering["cluster_lat"],
                "hotspot_lon": clustering["cluster_lon"],
                "clustering": {
                    "cells_occupied": clustering["cells_occupied"],
                    "winning_cell_count": clustering["winning_cell_count"],
                    "fallback_reason": clustering["fallback_reason"],
                    "distance_from_average_m": clustering["distance_from_average_m"],
                },
            }
        )
    return species_list
