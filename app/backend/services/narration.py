import re

from anthropic import Anthropic

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 300
TEMPERATURE = 1

LOCATION_GUIDANCE = """Infer where in the world this walk is taking place from the coordinates and the
species themselves — that's not a species fact, so you may use your own
knowledge for it. Don't state or imply a time of day, season, or "today" —
you have no way of knowing when the walk is actually happening."""

GROUNDED_FACTS_GUIDANCE = """Any fact you state about a species (behaviour, appearance, range, diet, etc.)
must come only from that species' Wikipedia extract above — never invent,
guess, or round up to an absolute claim beyond what the extract literally
says (e.g. a list of specific countries is not "every continent" or
"worldwide"). Translate technical wording into plain, warm language a
curious visitor with no biology background would enjoy, without exaggerating
it. It's good to include a fact or reference from each extract where it fits
naturally. A habitat or environment detail (e.g. "wetlands") must stay tied
to the one species whose extract actually supports it, phrased as true only
for that species in that moment — never a blanket claim about the whole walk
area (e.g. "the mallards dabble here in the wetlands" is fine, "throughout
these wetlands" is not, since habitat can vary across a geographic area even
where one species' extract mentions it). A general place name inferred from
the coordinates is fine on its own. When a fact is about where a species is
or isn't found, name the actual place from the extract
(e.g. "the Iberian peninsula") — a vague absolute like "found nowhere else
on Earth" with no place named could be misread as this one walk's exact spot
being the species' entire range. Never use unqualified absolute-scope words
like "worldwide," "everywhere," or "across the globe" for a species' range
unless the extract itself states that scope — a specific list of
regions/countries is not "worldwide." Similarly, never describe a species as
native/found "in this region" or "right here" when the extract's actual
range is broader than the walk's local area — name the actual region from
the extract (e.g. "the Western Palearctic") instead of implying the walk's
location is the whole range."""


def build_narrative_prompt(species_list: list[dict]) -> str:
    lines = []
    for i, sp in enumerate(species_list, 1):
        extract_text = sp["extract"] or "(no Wikipedia article found)"
        lines.append(
            f"{i}. {sp['common_name']} ({sp['species']}) at "
            f"({sp['hotspot_lat']:.4f}, {sp['hotspot_lon']:.4f})\n"
            f"   Wikipedia extract: {extract_text}"
        )
    species_block = "\n".join(lines)

    return f"""You are narrating a nature walk, in the style of a wildlife
documentary narrator — full of wonder, adventure, and a sense of discovery.

The walk visits these {len(species_list)} species, in order, each at its own
GPS coordinate, with a Wikipedia extract already looked up for each:

{species_block}

{LOCATION_GUIDANCE} {GROUNDED_FACTS_GUIDANCE}

Write a single flowing narrative guide of 120-130 words (about 45 seconds
spoken aloud) for a walker following this route. Mention every one of the
{len(species_list)} species by name somewhere in the narrative — do not
summarize, group, or omit any of them, even briefly. Write continuous
narrated prose, not a list. Do not use markdown, and do not include a title
or heading — start straight into the narration."""


def generate_narrative(
    species_list: list[dict], client: Anthropic, on_response=None, **extra_kwargs
) -> str:
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
