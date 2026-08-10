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
        # Plain centroid, not the adaptive N×N density-grid clustering built
        # in prototypes/scripts/e2e_walk_spike_clustering.py — deliberately
        # deferred to PRD Slice 4 (density-cluster hotspots), which this
        # slice doesn't need since there's no waypoint/route yet (REQ-011).
        species_list.append(
            {
                "species": species,
                "count": len(records),
                "kingdom": records[0].get("kingdom", "?"),
                "hotspot_lat": sum(p[0] for p in points) / len(points),
                "hotspot_lon": sum(p[1] for p in points) / len(points),
            }
        )
    return species_list
