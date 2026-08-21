import re

import pytest

from services.anthropic_client import build_client
from services.narration import generate_narrative
from services.wikipedia_client import fetch_species_summary

# Judge uses the same model class as the narrator (Haiku) — an explicit,
# knowingly-accepted cost/quality trade-off for now (see
# WORK_SUMMARY_180826.md: Haiku-as-judge was ~2-2.5x cheaper than Sonnet 5
# and comparable quality across a handful of runs, not exhaustively
# validated). Swap here if that changes.
JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_MAX_TOKENS = 800

# Same fixed Retiro Park sample walk as prototypes/scripts/narration_eval_spike.py.
RETIRO_BIRDS_WALK = [
    {"common_name": "Eurasian Magpie", "species": "Pica pica", "lat": 40.414848, "lon": -3.684565},
    {"common_name": "Iberian Green Woodpecker", "species": "Picus sharpei", "lat": 40.413755, "lon": -3.684227},
    {"common_name": "Egyptian Goose", "species": "Alopochen aegyptiaca", "lat": 40.414395, "lon": -3.682108},
    {"common_name": "Black Swan", "species": "Cygnus atratus", "lat": 40.413864, "lon": -3.681692},
    {"common_name": "Mallard", "species": "Anas platyrhynchos", "lat": 40.415567, "lon": -3.683259},
]

# Fish and turtles observed in Retiro's lake (Estanque Grande) — carp
# presence confirmed live via GBIF in PLANNING_INTENT_QUERY_210726.md;
# the others are well-documented residents of the same lake.
RETIRO_FISH_AND_TURTLES_WALK = [
    {"common_name": "Common Carp", "species": "Cyprinus carpio", "lat": 40.415100, "lon": -3.683600},
    {"common_name": "Red-eared Slider", "species": "Trachemys scripta", "lat": 40.415300, "lon": -3.683900},
    {"common_name": "Goldfish", "species": "Carassius auratus", "lat": 40.414900, "lon": -3.683400},
    {"common_name": "Koi", "species": "Cyprinus rubrofuscus", "lat": 40.415500, "lon": -3.683200},
    {"common_name": "European Pond Turtle", "species": "Emys orbicularis", "lat": 40.414700, "lon": -3.684100},
]

# A different continent/climate, to check the grounded-prompt guidance
# generalises beyond a single Retiro Park sample.
CENTRAL_PARK_NYC_WALK = [
    {"common_name": "Eastern Gray Squirrel", "species": "Sciurus carolinensis", "lat": 40.782865, "lon": -73.965355},
    {"common_name": "American Robin", "species": "Turdus migratorius", "lat": 40.781500, "lon": -73.966800},
    {"common_name": "Red-tailed Hawk", "species": "Buteo jamaicensis", "lat": 40.783600, "lon": -73.968100},
    {"common_name": "Mute Swan", "species": "Cygnus olor", "lat": 40.779400, "lon": -73.970700},
    {"common_name": "Canada Goose", "species": "Branta canadensis", "lat": 40.780200, "lon": -73.969200},
]

SAMPLE_WALKS = {
    "retiro_birds": RETIRO_BIRDS_WALK,
    "retiro_fish_and_turtles": RETIRO_FISH_AND_TURTLES_WALK,
    "central_park_nyc": CENTRAL_PARK_NYC_WALK,
}

# ── Programmatic checks — ported verbatim from narration_eval_spike.py ──

LENGTH_MAX_CHARS = 1000

TIME_REFERENCE_PATTERN = re.compile(
    r"\b(today|tonight|this morning|this afternoon|this evening|"
    r"right now|currently|at dawn|at dusk|sunrise|sunset|"
    r"this spring|this summer|this autumn|this fall|this winter|this season)\b",
    re.IGNORECASE,
)

DASH_PAUSE_PATTERN = re.compile(r"[—–]|\s-\s")


def check_length(narrative):
    length = len(narrative)
    passed = length <= LENGTH_MAX_CHARS
    return {"passed": passed, "detail": f"{length} chars (max {LENGTH_MAX_CHARS})"}


def check_no_time_references(narrative):
    matches = TIME_REFERENCE_PATTERN.findall(narrative)
    passed = len(matches) == 0
    return {"passed": passed, "detail": "none found" if passed else f"found: {matches}"}


def check_no_dash_pauses(narrative):
    matches = DASH_PAUSE_PATTERN.findall(narrative)
    passed = len(matches) == 0
    return {"passed": passed, "detail": "none found" if passed else f"{len(matches)} found"}


def check_mentions_all_species(narrative, species_list):
    lower_narrative = narrative.lower()
    missing = [sp["common_name"] for sp in species_list if sp["common_name"].lower() not in lower_narrative]
    passed = len(missing) == 0
    return {"passed": passed, "detail": "all species mentioned" if passed else f"missing: {missing}"}


# ── LLM judge — faithfulness + habitat claims, ported verbatim from
# narration_eval_spike.py (JUDGE_TOOL, build_judge_prompt, severity split). ──

JUDGE_TOOL = {
    "name": "report_narrative_quality",
    "description": "Assess a nature-walk narrative for faithfulness to its Wikipedia sources and ungrounded habitat claims.",
    "input_schema": {
        "type": "object",
        "properties": {
            "unsupported_species_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "description": "The narrative phrase making the claim"},
                        "severity": {
                            "type": "string", "enum": ["minor", "major"],
                            "description": "'minor' = a harmless stylistic flourish or loose paraphrase that "
                                           "is still roughly consistent with the extract (e.g. 'elegant', "
                                           "'devoted care' for a fact the extract states more plainly). "
                                           "'major' = a specific fact that is fabricated, wrong, or has no "
                                           "basis in the extract at all (e.g. an invented behaviour, wrong "
                                           "range, wrong relationship).",
                        },
                    },
                    "required": ["claim", "severity"],
                },
                "description": "Species facts (behaviour, appearance, range, diet, etc.) stated in the "
                                "NARRATIVE that are not clearly supported by that species' Wikipedia extract.",
            },
            "unsupported_habitat_claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "description": "The narrative phrase making the claim"},
                        "severity": {
                            "type": "string", "enum": ["minor", "major"],
                            "description": "'minor' = the habitat term IS supported by some species' "
                                           "extract, just attached to a different species or sentence than "
                                           "the one that actually supports it (e.g. 'wetlands' from the "
                                           "mallard's extract used near the goose instead). 'major' = a "
                                           "broad claim describing the whole walk/area (e.g. \"Madrid's "
                                           "wetlands\"), or a habitat term with no support in any extract "
                                           "at all.",
                        },
                    },
                    "required": ["claim", "severity"],
                },
                "description": "Specific, named habitat/environment claims (e.g. 'wetlands', 'rainforest') "
                                "made IN THE NARRATIVE, attributing a specific ecological requirement to a "
                                "species. A general place-name inference (e.g. 'Madrid'), a generic unnamed "
                                "word ('habitat', 'home', 'surroundings'), or a general description of the "
                                "walk's own visible setting (e.g. 'buildings and open spaces' for a walk "
                                "through a city) is fine and should NOT be listed here.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-3 plain-text sentences explaining these judgments, for a human reviewer "
                                "to sanity-check. Plain prose only — no XML/markdown tags.",
            },
        },
        "required": ["unsupported_species_claims", "unsupported_habitat_claims", "reasoning"],
    },
}


def build_judge_prompt(narrative, species_list):
    lines = []
    for i, sp in enumerate(species_list, 1):
        extract_text = sp["extract"] or "(no Wikipedia article found)"
        lines.append(f"{i}. {sp['common_name']} ({sp['species']}): {extract_text}")
    extracts_block = "\n".join(lines)

    return f"""You are grading the NARRATIVE below. The WIKIPEDIA EXTRACTS are
reference material for checking it against — they are the source of truth
for species facts, not something being graded themselves. Every claim you
report must be wording that actually appears in the NARRATIVE, never a term
that only appears in the extracts.

Also treat this as an unsupported species claim: a range/distribution fact
(e.g. "found nowhere else on Earth") stated without naming the actual place
from its extract (e.g. "the Iberian peninsula"), since a listener standing
at this one walk's location could misread it as this exact spot being the
species' entire range rather than the true, larger area. Mark it "major" if
no place is named anywhere nearby in the narrative; "minor" if the place is
named elsewhere in the narrative but not right next to this specific claim.

NARRATIVE:
{narrative}

WIKIPEDIA EXTRACTS:
{extracts_block}

Call report_narrative_quality with your findings."""


def split_by_severity(claims):
    major = [c["claim"] for c in claims if c["severity"] == "major"]
    minor = [c["claim"] for c in claims if c["severity"] == "minor"]
    return major, minor


def severity_detail(major, minor, none_found_label):
    if not (major or minor):
        return none_found_label
    return "; ".join(filter(None, [
        f"major: {major}" if major else "",
        f"minor (not failed): {minor}" if minor else "",
    ]))


def run_llm_judge(narrative, species_list, client):
    prompt = build_judge_prompt(narrative, species_list)
    response = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=0.2,
        tools=[JUDGE_TOOL],
        tool_choice={"type": "tool", "name": "report_narrative_quality"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = tool_use.input

    species_major, species_minor = split_by_severity(result["unsupported_species_claims"])
    habitat_major, habitat_minor = split_by_severity(result["unsupported_habitat_claims"])

    return {
        "faithfulness": {
            "passed": len(species_major) == 0,
            "detail": severity_detail(species_major, species_minor, "grounded"),
        },
        "no_habitat_claims": {
            "passed": len(habitat_major) == 0,
            "detail": severity_detail(habitat_major, habitat_minor, "none found"),
        },
        "reasoning": result["reasoning"],
    }


@pytest.fixture(scope="session", params=list(SAMPLE_WALKS.keys()))
def grounded_narrative_and_judgement(request):
    walk_name = request.param
    client = build_client()
    species_list = [
        {**sp, **fetch_species_summary(sp["common_name"], sp["species"])}
        for sp in SAMPLE_WALKS[walk_name]
    ]
    narrative = generate_narrative(species_list, client)
    judgement = run_llm_judge(narrative, species_list, client)
    mentions_check = check_mentions_all_species(narrative, species_list)
    print(
        f"\n{'=' * 70}\n"
        f"WALK: {walk_name}\n"
        f"NARRATIVE ({len(narrative)} chars):\n{narrative}\n"
        f"{'-' * 70}\n"
        f"mentions_all_species: {'PASS' if mentions_check['passed'] else 'FAIL'} — {mentions_check['detail']}\n"
        f"faithfulness: {'PASS' if judgement['faithfulness']['passed'] else 'FAIL'} — {judgement['faithfulness']['detail']}\n"
        f"no_habitat_claims: {'PASS' if judgement['no_habitat_claims']['passed'] else 'FAIL'} — {judgement['no_habitat_claims']['detail']}\n"
        f"judge reasoning: {judgement['reasoning']}\n"
        f"{'=' * 70}\n"
    )
    return narrative, species_list, judgement


@pytest.mark.eval
def test_narrative_is_within_the_length_backstop(grounded_narrative_and_judgement):
    narrative, _, _ = grounded_narrative_and_judgement
    check = check_length(narrative)
    assert check["passed"], check["detail"]


@pytest.mark.eval
def test_narrative_has_no_time_of_day_or_season_references(grounded_narrative_and_judgement):
    narrative, _, _ = grounded_narrative_and_judgement
    check = check_no_time_references(narrative)
    assert check["passed"], check["detail"]


@pytest.mark.eval
def test_narrative_has_no_dash_pauses(grounded_narrative_and_judgement):
    narrative, _, _ = grounded_narrative_and_judgement
    check = check_no_dash_pauses(narrative)
    assert check["passed"], check["detail"]


@pytest.mark.eval
def test_narrative_mentions_all_species(grounded_narrative_and_judgement):
    narrative, species_list, _ = grounded_narrative_and_judgement
    check = check_mentions_all_species(narrative, species_list)
    assert check["passed"], check["detail"]


@pytest.mark.eval
def test_narrative_has_no_major_unsupported_species_claims(grounded_narrative_and_judgement):
    _, _, judgement = grounded_narrative_and_judgement
    assert judgement["faithfulness"]["passed"], judgement["faithfulness"]["detail"]


@pytest.mark.eval
def test_narrative_has_no_major_unsupported_habitat_claims(grounded_narrative_and_judgement):
    _, _, judgement = grounded_narrative_and_judgement
    assert judgement["no_habitat_claims"]["passed"], judgement["no_habitat_claims"]["detail"]
