import os

import google.auth
from anthropic import Anthropic
from google.cloud import secretmanager

MODEL = "claude-haiku-4-5-20251001"
SECRET_ID = "anthropic-api-key"

TAXON_GUIDANCE = """You turn a nature-walk request into a single GBIF taxon
filter by calling produce_gbif_query.

- taxonRank must be one of: kingdom, phylum, class, order, family, genus.
- taxonValue is the scientific or common name at that rank (e.g. "Aves" for birds).
- Never use unranked clade names (e.g. "Vertebrata", "Tetrapoda") — they
  don't resolve reliably in GBIF's taxonomy.
- Only one taxon filter is supported. If the request names multiple
  distinct groups (e.g. "birds and insects"), pick whichever group is
  mentioned first in the request — do not return taxonFilter: null just
  because more than one group was named.
- If the request has no clear taxonomic signal, return taxonFilter: null.
  Do not guess.
"""

QUERY_SCHEMA_TOOL = {
    "name": "produce_gbif_query",
    "description": (
        "Translate the user's natural-language nature-walk request into a "
        "single GBIF taxon filter, or none if there's no clear taxonomic signal."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "taxonFilter": {
                "type": ["object", "null"],
                "description": (
                    "A single scientific rank + name pair, never a numeric "
                    "key. Null if the request has no clear taxonomic signal."
                ),
                "properties": {
                    "taxonRank": {
                        "type": "string",
                        "enum": ["kingdom", "phylum", "class", "order", "family", "genus"],
                    },
                    "taxonValue": {"type": "string"},
                },
                "required": ["taxonRank", "taxonValue"],
            }
        },
        "required": ["taxonFilter"],
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


def resolve_taxon_filter(query: str, client, on_response=None, **extra_kwargs) -> dict | None:
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
    return tool_use.input["taxonFilter"]
