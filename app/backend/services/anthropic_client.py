import os

from anthropic import Anthropic

from services.prompts.taxon_resolution import QUERY_SCHEMA_TOOL, TAXON_GUIDANCE
from services.secret_manager_client import fetch_secret

MODEL = "claude-haiku-4-5-20251001"
SECRET_ID = "anthropic-api-key"


def build_client() -> Anthropic:
    """Picks the key source by environment: Secret Manager when deployed on
    Cloud Run (REQ-005; K_SERVICE is set automatically there, never locally —
    tech debt: relies on a Cloud-Run-specific platform var rather than an
    explicit config flag we control), the local ANTHROPIC_API_KEY env var
    (.env) otherwise."""
    return Anthropic(api_key=resolve_api_key())


def resolve_api_key() -> str | None:
    if os.environ.get("K_SERVICE"):
        return fetch_secret(SECRET_ID)
    return None


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
