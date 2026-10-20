TAXON_GUIDANCE = """You turn a nature-walk request into a list of GBIF taxon
filters by calling produce_gbif_query.

- taxonRank must be one of: kingdom, phylum, class, order, family, genus.
- taxonValue is the scientific or common name at that rank (e.g. "Aves" for birds).
- Never use unranked clade names (e.g. "Vertebrata", "Tetrapoda") — they
  don't resolve reliably in GBIF's taxonomy.
- taxonFilters is a list, so it can hold more than one entry. If the
  request names multiple distinct groups (e.g. "birds and insects" or
  "a mix of birds, plants and mammals"), return one entry per group.
- Some lay terms don't map to a single GBIF rank and must be expanded into
  several entries covering the real groups involved:
  - "reptiles" -> four class-rank entries: Crocodylia, Squamata, Testudines,
    Sphenodontia (there is no single "Reptilia" class in GBIF's backbone).
  - "fish" -> seven entries covering the highest-volume fish groups:
    order-rank Perciformes, Cypriniformes, Scorpaeniformes, Gadiformes,
    Clupeiformes, Salmoniformes, plus class-rank Elasmobranchii (sharks
    and rays) — most ray-finned fish have no single GBIF class.
- IMPORTANT, non-standard rule specific to this app's GBIF setup: whenever
  "Testudines" is the taxonValue — whether the request says "turtles",
  "tortoises", "terrapins", or is part of a broader "reptiles" request —
  taxonRank MUST always be "class", never "order". This overrides normal
  biological convention (where Testudines is usually an order); GBIF's
  backbone taxonomy has no single "Reptilia" class, so this app queries
  Testudines at class rank on purpose. Do not use order for Testudines
  under any circumstances.
- If the request has no clear taxonomic signal, return an empty list.
  Do not guess.
"""

QUERY_SCHEMA_TOOL = {
    "name": "produce_gbif_query",
    "description": (
        "Translate the user's natural-language nature-walk request into a "
        "list of GBIF taxon filters, empty if there's no clear taxonomic signal."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "taxonFilters": {
                "type": "array",
                "description": (
                    "One entry per distinct taxon group named or implied by "
                    "the request. Each entry is a scientific rank + name "
                    "pair, never a numeric key. Empty if there's no clear "
                    "taxonomic signal."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "taxonRank": {
                            "type": "string",
                            "enum": ["kingdom", "phylum", "class", "order", "family", "genus"],
                        },
                        "taxonValue": {"type": "string"},
                    },
                    "required": ["taxonRank", "taxonValue"],
                },
            }
        },
        "required": ["taxonFilters"],
    },
}
