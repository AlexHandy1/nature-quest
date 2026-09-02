import json

from services.anthropic_client import QUERY_SCHEMA_TOOL, TAXON_GUIDANCE

MODEL = "google/gemini-3.7-flash"
REQUEST_TIMEOUT = 30.0

QUERY_SCHEMA_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": QUERY_SCHEMA_TOOL["name"],
        "description": QUERY_SCHEMA_TOOL["description"],
        "parameters": QUERY_SCHEMA_TOOL["input_schema"],
    },
}


def resolve_taxon_filters(query: str, client, on_response=None, **extra_kwargs) -> list[dict]:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": TAXON_GUIDANCE},
            {"role": "user", "content": query},
        ],
        tools=[QUERY_SCHEMA_TOOL_OPENAI],
        tool_choice={"type": "function", "function": {"name": "produce_gbif_query"}},
        timeout=REQUEST_TIMEOUT,
        **extra_kwargs,
    )
    if on_response is not None:
        on_response(response)

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise ValueError("OpenRouter response did not include a tool call")

    arguments = json.loads(tool_calls[0].function.arguments)
    return arguments["taxonFilters"]
