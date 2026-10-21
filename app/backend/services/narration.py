import re

from anthropic import Anthropic

from services.prompts.narration import REFUSAL_SENTINEL, build_narrative_prompt

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300
TEMPERATURE = 1


def generate_narrative(
    species_list: list[dict], client: Anthropic, on_response=None, **extra_kwargs
) -> str | None:
    prompt = build_narrative_prompt(species_list)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
        **extra_kwargs,
    )
    if on_response is not None:
        on_response(response)
    narrative = "".join(block.text for block in response.content if block.type == "text").strip()
    if narrative == REFUSAL_SENTINEL:
        return None
    return sanitize_dash_pauses(narrative)


def sanitize_dash_pauses(narrative: str) -> str:
    """Em/en dashes and spaced hyphens read poorly (or not at all) as pauses
    by TTS models — rather than rely on the LLM to avoid them reliably,
    deterministically swap them for a comma after generation. A hyphenated
    compound word (e.g. "well-known") has no surrounding spaces so it's
    untouched."""
    narrative = re.sub(r"\s*[—–]\s*", ", ", narrative)
    narrative = re.sub(r"\s-\s", ", ", narrative)
    return narrative
