import httpx

GBIF_SPECIES_MATCH_URL = "https://api.gbif.org/v1/species/match"
MIN_FUZZY_CONFIDENCE = 85


def resolve_taxon_key(taxon_rank: str, taxon_value: str) -> int | None:
    match = _fetch_species_match(taxon_value, taxon_rank)
    match_type = match.get("matchType")
    is_accepted = match_type == "EXACT" or (
        match_type == "FUZZY" and match.get("confidence", 0) >= MIN_FUZZY_CONFIDENCE
    )
    if is_accepted:
        return match.get(f"{taxon_rank}Key")
    return None


def _fetch_species_match(taxon_value: str, taxon_rank: str) -> dict:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL,
        params={"name": taxon_value, "rank": taxon_rank.upper()},
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()
