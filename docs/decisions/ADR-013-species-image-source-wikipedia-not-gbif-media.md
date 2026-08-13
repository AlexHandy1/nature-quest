# ADR-013: Species Images Sourced from Wikipedia, Not GBIF Occurrence Media

## Status
Accepted

## Date
2026-08-13

## Context

This session added per-species enrichment (common name, image, GBIF link) to the results panel, following on from the mobile/desktop UX prototyping in the previous session. Common name and the GBIF link were straightforward (GBIF's `species/{key}/vernacularNames`, and `species_key` which the backend already fetched but hadn't threaded through to the frontend). The image source needed a real decision.

A live check against real GBIF occurrence data for Retiro Park confirmed that occurrence records already carry a `media` array (photo URLs, mostly sourced from iNaturalist) at zero extra cost — no new API call, since the pipeline already fetches these records during ranking. The first implementation used this: the first occurrence with a non-empty `media` list won.

This was challenged before being built out further: GBIF occurrence media is an individual citizen-science observation's attached photo, not a curated or vetted "this represents the species" image. It could be a blurry field shot, a close-up of a footprint or feather rather than the animal, or (rarer but real in citizen-science data) attached to a misidentified record. GBIF's occurrence API has no quality or representativeness signal to select on.

## Decision

Use the **Wikipedia summary API** (`en.wikipedia.org/api/rest_v1/page/summary/{title}`) for the species image instead, looked up by common name first (falling back to scientific name on a missing article or disambiguation page) — the same approach already validated in `prototypes/scripts/e2e_walk_spike_full_validation.py`. Each species' Wikipedia article has one editorially-curated infobox image, which is a much stronger signal for "this correctly and clearly represents the species" than an arbitrary local sighting photo.

This also sets up reuse for a possible future narrative-description feature (the same Wikipedia summary payload carries an `extract` field), which the prototype script already used for exactly that purpose — not a reason to build description support now, but a reason this choice doesn't need revisiting if that feature gets picked up later.

## Alternatives Considered

### GBIF occurrence media (first occurrence with a photo)
- Pros: zero extra network call — the data is already fetched during ranking. Real, local, in-area photos.
- Cons: no representativeness or quality signal at all; can surface a poor, unrepresentative, or (rarely) misidentified photo.
- Rejected because: an identification aid needs to reliably show what the species actually looks like, not prove a specific local sighting happened.

### iNaturalist taxon API (`api.inaturalist.org/v1/taxa`, `default_photo` field)
- Pros: community-vetted "default" photo per taxon, better signal than a random occurrence photo.
- Cons: a third external dependency, unvalidated anywhere in this codebase.
- Rejected because: Wikipedia is already a validated, precedented choice (via the prototype script) and gets the same "curated, not random" property without adding a new untested integration.

### GBIF occurrence media, filtered/sampled better (e.g. license-restricted, pick from multiple candidates)
- Pros: incremental improvement over "first occurrence with media," no new dependency.
- Cons: still fundamentally "a real citizen sighting" with no actual quality guarantee — doesn't solve the underlying concern, just narrows it slightly.
- Rejected because: doesn't address the core problem (no representativeness signal).

## Consequences

- One more external dependency (Wikipedia's REST API) alongside GBIF and Anthropic. Requires a descriptive `User-Agent` header per Wikimedia's API etiquette (GBIF has no equivalent requirement, so `gbif_client.py` doesn't set one — not an inconsistency, a different external API's constraint).
- Common name and image lookups are sequential per species (image lookup needs the resolved common name first), parallelized across species instead — `services/species_enrichment.py`, capped at 3 concurrent species like the rest of the pipeline's external-call fan-out.
- Enrichment (`fetch_common_name` + `fetch_species_image`) runs only on the final ~5 selected species, not every candidate scanned during ranking — each species costs one GBIF call plus one Wikipedia call, so this is deliberately scoped tightly.
- A live smoke test (`prototypes/scripts/species_enrichment_smoke.py`, throwaway) surfaced a real GBIF data-quality bug during this session unrelated to the image decision: `fetch_common_name` picking the first English-tagged vernacular name could surface a banding-code abbreviation (e.g. "COMO" for Common Moorhen) tagged `language: eng` by a single low-quality source, ahead of the real name repeated across many sources. Fixed by majority-voting across English-tagged names instead of taking the first match — see `services/gbif_client.py`'s `fetch_common_name` docstring.
