#!/usr/bin/env python3
"""
THROWAWAY — species_enrichment_smoke.py
Not a permanent test. One-off check that this session's new backend pieces
(services/species_enrichment.py: GBIF common-name lookup + Wikipedia image
lookup, wired into routers/query.py) actually return real data against live
GBIF + Wikipedia for Retiro Park, before any frontend work is built against
it. Skips the LLM taxon-resolution step entirely (hardcodes class=Aves) since
that step isn't what's being validated here.

Run:
  cd app/backend && source venv/bin/activate && \\
    python ../../prototypes/scripts/species_enrichment_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "app", "backend"))

from services.gbif_client import GBIF_POLYGON, fetch_top_species, polygon_centroid  # noqa: E402
from services.species_enrichment import enrich_species  # noqa: E402
from services.waypoints import order_waypoints  # noqa: E402

AVES_CLASS_KEY = 212


def main():
    print(f"Fetching top species for class=Aves (key={AVES_CLASS_KEY}) in Retiro Park...")
    species = fetch_top_species(
        [{"taxon_rank": "class", "taxon_key": AVES_CLASS_KEY}], polygon=GBIF_POLYGON
    )
    print(f"fetch_top_species -> {len(species)} species\n")

    center_lat, center_lon = polygon_centroid(GBIF_POLYGON)
    ordered = order_waypoints(species, center_lat, center_lon)

    print("Enriching with common_name (GBIF) + image_url (Wikipedia)...\n")
    enriched = enrich_species(ordered)

    print("--- enriched species ---")
    for s in enriched:
        print(f"  species={s['species']!r}")
        print(f"    species_key = {s.get('species_key')}")
        print(f"    common_name = {s.get('common_name')!r}")
        print(f"    image_url   = {s.get('image_url')!r}")

    has_key = sum(1 for s in enriched if s.get("species_key") is not None)
    has_common_name = sum(1 for s in enriched if s.get("common_name"))
    has_image = sum(1 for s in enriched if s.get("image_url"))
    print(
        f"\n{has_key}/{len(enriched)} have species_key, "
        f"{has_common_name}/{len(enriched)} have common_name, "
        f"{has_image}/{len(enriched)} have image_url"
    )


if __name__ == "__main__":
    main()
