from concurrent.futures import ThreadPoolExecutor

from services.wikipedia_client import fetch_species_summary

MAX_CONCURRENT_ENRICHMENT_REQUESTS = 3


def enrich_species(species_list: list[dict]) -> list[dict]:
    """Adds image_url/extract (Wikipedia, keyed off each species' common_name
    - already joined from the dimension table by services.species_index, not
    looked up here) to each species dict. Only call this on the final
    selected species list, not every candidate scanned during ranking - each
    species costs one Wikipedia call."""
    if not species_list:
        return []

    max_workers = min(len(species_list), MAX_CONCURRENT_ENRICHMENT_REQUESTS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_enrich_one, species_list))


def _enrich_one(species: dict) -> dict:
    summary = fetch_species_summary(species.get("common_name"), species["species"])
    return {**species, **summary}
