import os

import google.auth
from anthropic import Anthropic
from google.cloud import secretmanager

MODEL = "claude-haiku-4-5-20251001"
SECRET_ID = "anthropic-api-key"

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


def build_client() -> Anthropic:
    """Picks the key source by environment: Secret Manager when deployed on
    Cloud Run (REQ-005; K_SERVICE is set automatically there, never locally —
    tech debt: relies on a Cloud-Run-specific platform var rather than an
    explicit config flag we control), the local ANTHROPIC_API_KEY env var
    (.env) otherwise."""
    return Anthropic(api_key=resolve_api_key())


def resolve_api_key() -> str | None:
    if os.environ.get("K_SERVICE"):
        return _fetch_api_key_from_secret_manager()
    return None


def _fetch_api_key_from_secret_manager() -> str:
    _, project_id = google.auth.default()
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{SECRET_ID}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


def resolve_taxon_filters(query: str, client, on_response=None, **extra_kwargs) -> list[dict]:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=TAXON_GUIDANCE,
        tools=[QUERY_SCHEMA_TOOL],
        tool_choice={"type": "tool", "name": "produce_gbif_query"},
        messages=[{"role": "user", "content": query}],
        **extra_kwargs,
    )
    if on_response is not None:
        on_response(response)
    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input["taxonFilters"]
