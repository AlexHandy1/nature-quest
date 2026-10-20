import os

from services.prompts.taxon_resolution import QUERY_SCHEMA_TOOL, TAXON_GUIDANCE
from services.secret_manager_client import fetch_secret

MODEL = "claude-haiku-4-5-20251001"
SECRET_ID = "anthropic-api-key"


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
        tool_choice={"type": "tool", "name": QUERY_SCHEMA_TOOL["name"]},
        messages=[{"role": "user", "content": query}],
        **extra_kwargs,
    )
    if on_response is not None:
        on_response(response)
    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input["taxonFilters"]
