from concurrent.futures import ThreadPoolExecutor

from services.gbif_client import fetch_common_name
from services.wikipedia_client import fetch_species_image

MAX_CONCURRENT_ENRICHMENT_REQUESTS = 3


def enrich_species(species_list: list[dict]) -> list[dict]:
    """Adds common_name (GBIF) and image_url (Wikipedia, keyed off the
    resolved common name) to each species dict. Only call this on the final
    selected species list, not every candidate scanned during ranking —
    each species costs one GBIF call plus one Wikipedia call."""
    if not species_list:
        return []

    max_workers = min(len(species_list), MAX_CONCURRENT_ENRICHMENT_REQUESTS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_enrich_one, species_list))


def _enrich_one(species: dict) -> dict:
    common_name = fetch_common_name(species.get("species_key"))
    image_url = fetch_species_image(common_name, species["species"])
    return {**species, "common_name": common_name, "image_url": image_url}
