#!/usr/bin/env python3
"""
PROTOTYPE — narration_eval_spike.py
Question: can we define "good narration" in automatable terms and score both
the baseline (names+locations only) and Wikipedia-grounded narrative variants
against it — proving the evals actually distinguish a worse narrative from a
better one, not just rubber-stamping whatever comes out?

Forked from narration_wikipedia_spike.py (copy-not-edit checkpoint convention
— that script stays a stable reference) with one addition: an eval pass after
generation. Five criteria, agreed this session (a sixth, jargon/non-expert
readability via an LLM judge, was tried and dropped — its findings weren't
reliable enough to act on; see prototypes/README.md open items):

  Programmatic (regex/length, no LLM call):
    - length            <=1000 characters (generation targets 120-130 words
                          as the primary control; this is a hard backstop)
    - no_time_refs       no time-of-day/season/"today" statements — no
                          awareness of when a user actually runs this
    - no_dash_pauses      no "—"/" - " artificial pauses — unreliable for
                          TTS. Not left to the LLM: sanitize_dash_pauses()
                          deterministically swaps dashes for a comma right
                          after generation, before eval or rendering, so
                          this check should always pass — kept as a
                          regression check on the sanitizer itself.

  LLM judge (Sonnet 5 — a different model class than the Haiku narrator, so
  it isn't grading its own homework), one combined structured-output call
  per narrative covering both semantic criteria at once:
    - faithfulness        species facts only from the retrieved Wikipedia
                          extracts, not outside knowledge. Each unsupported
                          claim gets a minor/major severity — only "major"
                          (fabricated/wrong) fails the check; "minor" (a
                          loose paraphrase or stylistic flourish) is still
                          surfaced in the report for a human to see, just
                          doesn't fail it.
    - no_habitat_claims   no specific habitat/environment claims (e.g.
                          "wetlands") ungrounded in an extract — a general
                          place-name inference from coordinates is fine.
                          Same minor/major severity split as faithfulness:
                          "minor" if the term is real (supported by some
                          species' extract) but attached to the wrong
                          species/sentence; "major" if it describes the
                          whole walk or has no support anywhere.

  Judge output includes a free-text `reasoning` field, printed to console and
  shown in the HTML report, so every judgment can be sanity-checked by a
  human rather than trusted blind.

Runs the eval against BOTH narrative variants (not just the grounded one) —
deliberately, so the report shows whether the checks actually catch the
baseline's known problems (e.g. no traceable source at all, invented habitat
framing like "Madrid's wetlands") rather than only ever passing.

Cost is tracked in three groups: narrative generation (Haiku, both variants),
eval judging (Sonnet 5 by default, both variants), and total. `--judge-model`
swaps the judge to Haiku — the eval cost (~$0.018/pair with Sonnet 5) is the
dominant cost group by far, so it's worth checking whether a cheaper judge
still catches the same issues before assuming Sonnet 5 is required.

Throwaway. Do not promote to production.

Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/narration_eval_spike.py \
    [--judge-model claude-sonnet-5|claude-haiku-4-5-20251001] \
    2>&1 | tee prototypes/logs/narration_eval_$(date +%Y%m%d_%H%M%S).log
"""

import argparse
import html
import json
import os
import re
import sys
import time
import webbrowser
from urllib.parse import quote

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

NARRATIVE_MODEL = "claude-haiku-4-5-20251001"
NARRATIVE_MAX_TOKENS = 300
DEFAULT_JUDGE_MODEL = "claude-sonnet-5"
JUDGE_MAX_TOKENS = 800

# $/MTok, (input, output) — same figures as species_narrative_cost_experiment2.py's MODEL_PRICING table.
MODEL_PRICING_USD_PER_MTOK = {
    NARRATIVE_MODEL: (1.00, 5.00),
    DEFAULT_JUDGE_MODEL: (2.00, 10.00),
}

WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIPEDIA_USER_AGENT = "nature-quest-prototype/0.1"

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

# Same fixed sample walk as narration_tts_spike.py / narration_wikipedia_spike.py.
SAMPLE_WALK = {
    "species": [
        {"common_name": "Eurasian Magpie", "scientific_name": "Pica pica",
         "lat": 40.414848, "lon": -3.684565},
        {"common_name": "Iberian Green Woodpecker", "scientific_name": "Picus sharpei",
         "lat": 40.413755, "lon": -3.684227},
        {"common_name": "Egyptian Goose", "scientific_name": "Alopochen aegyptiaca",
         "lat": 40.414395, "lon": -3.682108},
        {"common_name": "Black Swan", "scientific_name": "Cygnus atratus",
         "lat": 40.413864, "lon": -3.681692},
        {"common_name": "Mallard", "scientific_name": "Anas platyrhynchos",
         "lat": 40.415567, "lon": -3.683259},
    ],
}


def header(title):
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")


# ── Wikipedia lookup — mirrors app/backend/services/wikipedia_client.py's
# resolution logic (common name first, scientific name fallback,
# disambiguation check); also keeps extract/url, which production discards. ──

def fetch_wikipedia_summary(title):
    url = f"{WIKIPEDIA_SUMMARY_URL}/{quote(title)}"
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": WIKIPEDIA_USER_AGENT})
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    if data.get("type") == "disambiguation" or not data.get("extract"):
        return None
    page_url = data.get("content_urls", {}).get("desktop", {}).get("page")
    return {"title": data.get("title"), "extract": data.get("extract"), "url": page_url}


def fetch_species_wikipedia_extracts(walk):
    extracts = {}
    start = time.monotonic()
    for sp in walk["species"]:
        summary = fetch_wikipedia_summary(sp["common_name"]) or fetch_wikipedia_summary(sp["scientific_name"])
        extracts[sp["scientific_name"]] = summary
        status = f"{GREEN}found{RESET}" if summary else f"{RED}not found{RESET}"
        print(f"  {sp['common_name']} ({sp['scientific_name']}): {status}")
    return extracts, time.monotonic() - start


# ── Narrative generation — same prompt as narration_wikipedia_spike.py ──

LOCATION_GUIDANCE = """Infer where in the world this walk is taking place from the coordinates and the
species themselves — that's not a species fact, so you may use your own
knowledge for it. Don't state or imply a time of day, season, or "today" —
you have no way of knowing when the walk is actually happening."""

BASELINE_FACTS_GUIDANCE = """You have only each species' name and location to work from — draw on your own
knowledge of these species, and weave in a sense of place and journey."""

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
being the species' entire range."""


def build_narrative_prompt(walk, extracts=None):
    lines = []
    for i, sp in enumerate(walk["species"], 1):
        line = f"{i}. {sp['common_name']} ({sp['scientific_name']}) at ({sp['lat']:.4f}, {sp['lon']:.4f})"
        if extracts is not None:
            summary = extracts.get(sp["scientific_name"])
            extract_text = summary["extract"] if summary else "(no Wikipedia article found)"
            line += f"\n   Wikipedia extract: {extract_text}"
        lines.append(line)
    species_block = "\n".join(lines)
    facts_guidance = GROUNDED_FACTS_GUIDANCE if extracts is not None else BASELINE_FACTS_GUIDANCE

    return f"""You are narrating a nature walk, in the style of a wildlife
documentary narrator — full of wonder, adventure, and a sense of discovery.

The walk visits these {len(walk['species'])} species, in order, each at its own
GPS coordinate{", with a Wikipedia extract already looked up for each" if extracts is not None else ""}:

{species_block}

{LOCATION_GUIDANCE} {facts_guidance}

Write a single flowing narrative guide of 120-130 words (about 45 seconds
spoken aloud) for a walker following this route. Write continuous narrated
prose, not a list. Do not use markdown, and do not include a title or
heading — start straight into the narration."""


def estimated_cost_usd(input_tokens, output_tokens, model):
    in_price, out_price = MODEL_PRICING_USD_PER_MTOK[model]
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


def sanitize_dash_pauses(narrative):
    """Em/en dashes and spaced hyphens read poorly (or not at all) as pauses
    by TTS models — rather than rely on the LLM to avoid them reliably,
    deterministically swap them for a comma after generation, before eval or
    rendering. A hyphenated compound word (e.g. "well-known") has no
    surrounding spaces so it's untouched."""
    narrative = re.sub(r"\s*[—–]\s*", ", ", narrative)
    narrative = re.sub(r"\s-\s", ", ", narrative)
    return narrative


def generate_narrative(prompt, client, label):
    print(f"\n  {DIM}--- {label} PROMPT ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.monotonic()
    response = client.messages.create(
        model=NARRATIVE_MODEL, max_tokens=NARRATIVE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.monotonic() - start
    narrative = "".join(block.text for block in response.content if block.type == "text").strip()
    narrative = sanitize_dash_pauses(narrative)
    input_tokens, output_tokens = response.usage.input_tokens, response.usage.output_tokens
    cost_usd = estimated_cost_usd(input_tokens, output_tokens, NARRATIVE_MODEL)

    print(f"\n  {DIM}--- {label} RESPONSE ({elapsed_s:.1f}s, dash-sanitized) ---{RESET}")
    print(f"  {narrative}")
    print(f"  {DIM}[in={input_tokens} out={output_tokens} cost=${cost_usd:.5f} model={NARRATIVE_MODEL}]{RESET}")

    return {
        "narrative": narrative, "elapsed_s": elapsed_s,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd,
    }


# ── Programmatic eval checks — pure functions over a narrative string ──

LENGTH_MAX_CHARS = 1000

TIME_REFERENCE_PATTERN = re.compile(
    r"\b(today|tonight|this morning|this afternoon|this evening|"
    r"right now|currently|at dawn|at dusk|sunrise|sunset|"
    r"this spring|this summer|this autumn|this fall|this winter|this season)\b",
    re.IGNORECASE,
)

# Em dash, en dash, or a hyphen used as a spaced mid-sentence pause. A plain
# hyphenated compound word (e.g. "well-known") has no surrounding spaces, so
# it isn't matched.
DASH_PAUSE_PATTERN = re.compile(r"[—–]|\s-\s")


def check_length(narrative):
    length = len(narrative)
    passed = length <= LENGTH_MAX_CHARS
    return {"name": "length", "passed": passed, "detail": f"{length} chars (max {LENGTH_MAX_CHARS})"}


def check_no_time_references(narrative):
    matches = TIME_REFERENCE_PATTERN.findall(narrative)
    passed = len(matches) == 0
    return {"name": "no_time_refs", "passed": passed, "detail": "none found" if passed else f"found: {matches}"}


def check_no_dash_pauses(narrative):
    matches = DASH_PAUSE_PATTERN.findall(narrative)
    passed = len(matches) == 0
    return {"name": "no_dash_pauses", "passed": passed, "detail": "none found" if passed else f"{len(matches)} found"}


PROGRAMMATIC_CHECKS = [check_length, check_no_time_references, check_no_dash_pauses]


# ── LLM judge (Sonnet 5) — faithfulness + habitat claims, one call ──────
# A third criterion (jargon/non-expert readability) was tried here and
# dropped: the judge's jargon_phrases findings weren't reliable enough to
# act on (see prototypes/README.md open items for the run that prompted
# this) — faithfulness and habitat-claim findings, by contrast, held up
# under review and are worth keeping.

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
                "description": "Specific habitat/environment claims (e.g. 'wetlands', 'rainforest') made IN "
                                "THE NARRATIVE. A general place-name inference (e.g. 'Madrid') is fine and "
                                "should NOT be listed here.",
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


def build_judge_prompt(narrative, walk, extracts):
    lines = []
    for i, sp in enumerate(walk["species"], 1):
        summary = extracts.get(sp["scientific_name"])
        extract_text = summary["extract"] if summary else "(no Wikipedia article found)"
        lines.append(f"{i}. {sp['common_name']} ({sp['scientific_name']}): {extract_text}")
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


def run_llm_judge(narrative, walk, extracts, client, label, judge_model):
    prompt = build_judge_prompt(narrative, walk, extracts)
    print(f"\n  {DIM}--- {label} JUDGE PROMPT ({judge_model}) ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.monotonic()
    response = client.messages.create(
        model=judge_model, max_tokens=JUDGE_MAX_TOKENS,
        tools=[JUDGE_TOOL], tool_choice={"type": "tool", "name": "report_narrative_quality"},
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.monotonic() - start
    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = tool_use.input
    input_tokens, output_tokens = response.usage.input_tokens, response.usage.output_tokens
    cost_usd = estimated_cost_usd(input_tokens, output_tokens, judge_model)

    print(f"\n  {DIM}--- {label} JUDGE RESPONSE ({elapsed_s:.1f}s) ---{RESET}")
    print(f"  {DIM}{json.dumps(result, indent=2)}{RESET}")
    print(f"  {DIM}[in={input_tokens} out={output_tokens} cost=${cost_usd:.5f} model={judge_model}]{RESET}")

    species_major, species_minor = split_by_severity(result["unsupported_species_claims"])
    habitat_major, habitat_minor = split_by_severity(result["unsupported_habitat_claims"])

    checks = [
        {
            "name": "faithfulness", "passed": len(species_major) == 0,
            "detail": severity_detail(species_major, species_minor, "grounded"),
        },
        {
            "name": "no_habitat_claims", "passed": len(habitat_major) == 0,
            "detail": severity_detail(habitat_major, habitat_minor, "none found"),
        },
    ]
    return checks, result["reasoning"], {"elapsed_s": elapsed_s, "input_tokens": input_tokens,
                                          "output_tokens": output_tokens, "cost_usd": cost_usd}


def run_eval(narrative, walk, extracts, client, label, judge_model):
    checks = [check(narrative) for check in PROGRAMMATIC_CHECKS]
    judge_checks, reasoning, judge_stats = run_llm_judge(narrative, walk, extracts, client, label, judge_model)
    checks.extend(judge_checks)
    return checks, reasoning, judge_stats


def print_eval_table(label, checks):
    print(f"\n  {BOLD}{label} — eval results{RESET}")
    for check in checks:
        icon = f"{GREEN}PASS{RESET}" if check["passed"] else f"{RED}FAIL{RESET}"
        print(f"    [{icon}] {check['name']:<20} {check['detail']}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--judge-model", default=DEFAULT_JUDGE_MODEL,
        choices=list(MODEL_PRICING_USD_PER_MTOK.keys()),
        help=f"Model for the LLM judge (default: {DEFAULT_JUDGE_MODEL}).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    judge_model = args.judge_model

    print(f"\n{BOLD}PROTOTYPE: Narration eval harness (baseline vs. Wikipedia-grounded){RESET}")
    print(f"{DIM}Question: do the agreed eval criteria actually distinguish narration quality?{RESET}")
    print(f"{DIM}judge_model={judge_model}{RESET}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print(f"\n{RED}ANTHROPIC_API_KEY is not set.{RESET}")
        sys.exit(1)
    client = Anthropic(api_key=anthropic_key)

    header("STEP 1: Fetch Wikipedia extracts")
    extracts, wiki_fetch_elapsed_s = fetch_species_wikipedia_extracts(SAMPLE_WALK)
    print(f"  {DIM}total fetch time: {wiki_fetch_elapsed_s:.1f}s{RESET}")

    header("STEP 2a: Baseline narrative (names + locations only)")
    baseline = generate_narrative(build_narrative_prompt(SAMPLE_WALK), client, "BASELINE")

    header("STEP 2b: Wikipedia-grounded narrative")
    grounded = generate_narrative(build_narrative_prompt(SAMPLE_WALK, extracts), client, "WIKIPEDIA-GROUNDED")

    header("STEP 3a: Evaluate baseline narrative")
    baseline_checks, baseline_reasoning, baseline_judge_stats = run_eval(
        baseline["narrative"], SAMPLE_WALK, extracts, client, "BASELINE", judge_model
    )
    print_eval_table("BASELINE", baseline_checks)

    header("STEP 3b: Evaluate Wikipedia-grounded narrative")
    grounded_checks, grounded_reasoning, grounded_judge_stats = run_eval(
        grounded["narrative"], SAMPLE_WALK, extracts, client, "WIKIPEDIA-GROUNDED", judge_model
    )
    print_eval_table("WIKIPEDIA-GROUNDED", grounded_checks)

    header("SUMMARY — cost & timing")
    generation_cost = baseline["cost_usd"] + grounded["cost_usd"]
    eval_cost = baseline_judge_stats["cost_usd"] + grounded_judge_stats["cost_usd"]
    total_cost = generation_cost + eval_cost
    generation_time = baseline["elapsed_s"] + grounded["elapsed_s"] + wiki_fetch_elapsed_s
    eval_time = baseline_judge_stats["elapsed_s"] + grounded_judge_stats["elapsed_s"]

    print(f"  {'Group':<28} {'Time':>8} {'Cost':>10}")
    print(f"  {'generation (Haiku x2 + wiki)':<28} {generation_time:>7.1f}s {'$' + format(generation_cost, '.5f'):>10}")
    print(f"  {f'eval judging ({judge_model} x2)':<28} {eval_time:>7.1f}s {'$' + format(eval_cost, '.5f'):>10}")
    print(f"  {'-' * 48}")
    print(f"  {'TOTAL':<28} {generation_time + eval_time:>7.1f}s {'$' + format(total_cost, '.5f'):>10}")

    baseline_pass_count = sum(c["passed"] for c in baseline_checks)
    grounded_pass_count = sum(c["passed"] for c in grounded_checks)
    print(f"\n  baseline:  {baseline_pass_count}/{len(baseline_checks)} checks passed")
    print(f"  grounded:  {grounded_pass_count}/{len(grounded_checks)} checks passed")

    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    html_path = generate_report_page(
        artifacts_dir, SAMPLE_WALK, extracts, wiki_fetch_elapsed_s, judge_model,
        baseline, baseline_checks, baseline_reasoning, baseline_judge_stats,
        grounded, grounded_checks, grounded_reasoning, grounded_judge_stats,
    )
    print(f"\n{GREEN}Opening report -> {html_path}{RESET}\n")
    webbrowser.open(f"file://{html_path}")


def render_check_rows(checks):
    """Check names are our own fixed strings (safe); detail/reasoning text
    is partly LLM-generated (e.g. quoted narrative fragments) and gets
    HTML-escaped — a live run surfaced the judge leaking stray
    "</reasoning>"/"</invoke>" tokens into its free-text output, which broke
    the page when inserted raw."""
    rows = ""
    for c in checks:
        status_class = "pass" if c["passed"] else "fail"
        status_text = "PASS" if c["passed"] else "FAIL"
        rows += (
            f'<tr class="{status_class}"><td>{html.escape(c["name"])}</td>'
            f'<td class="status">{status_text}</td><td>{html.escape(c["detail"])}</td></tr>'
        )
    return rows


def generate_report_page(
    artifacts_dir, walk, extracts, wiki_fetch_elapsed_s, judge_model,
    baseline, baseline_checks, baseline_reasoning, baseline_judge_stats,
    grounded, grounded_checks, grounded_reasoning, grounded_judge_stats,
):
    generation_cost = baseline["cost_usd"] + grounded["cost_usd"]
    eval_cost = baseline_judge_stats["cost_usd"] + grounded_judge_stats["cost_usd"]
    total_cost = generation_cost + eval_cost

    all_sources_html = ""
    for sp in walk["species"]:
        summary = extracts.get(sp["scientific_name"])
        if summary and summary.get("url"):
            all_sources_html += (
                f'<li><a href="{summary["url"]}" target="_blank" rel="noopener">'
                f'{sp["common_name"]}</a> — <span class="wiki-title">{summary["title"]}</span></li>'
            )
        else:
            all_sources_html += f'<li>{sp["common_name"]} — <span class="missing">no article found</span></li>'

    baseline_html = html.escape(baseline["narrative"]).replace("\n\n", "</p><p>").replace("\n", " ")
    grounded_html = html.escape(grounded["narrative"]).replace("\n\n", "</p><p>").replace("\n", " ")
    baseline_reasoning_html = html.escape(baseline_reasoning)
    grounded_reasoning_html = html.escape(grounded_reasoning)

    page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — narration eval harness</title>
<style>
  body {{ font-family: sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #222; }}
  #banner {{ display: inline-block; background: #ffd700; padding: 3px 12px; border-radius: 4px;
    font-size: 11px; font-weight: bold; margin-bottom: 16px; }}
  .columns {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .column {{ flex: 1; min-width: 360px; border: 1px solid #ddd; border-radius: 8px; padding: 16px 20px; }}
  .column h2 {{ font-size: 15px; margin-top: 0; }}
  .stats {{ font-size: 12px; color: #555; background: #f6f6f6; border-radius: 6px; padding: 8px 12px; margin-bottom: 14px; }}
  .stats div {{ margin: 2px 0; }}
  p {{ line-height: 1.6; }}
  .sources {{ margin-top: 18px; padding-top: 12px; border-top: 1px dashed #ddd; }}
  .sources h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; color: #666; margin: 0 0 6px; }}
  .sources ul {{ padding-left: 20px; margin: 0; font-size: 12px; }}
  .wiki-title {{ color: #666; font-size: 12px; }}
  .missing {{ color: #b00; font-size: 12px; }}
  table.checks {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }}
  table.checks td {{ padding: 4px 6px; border-bottom: 1px solid #eee; vertical-align: top; }}
  table.checks td.status {{ font-weight: bold; white-space: nowrap; }}
  tr.pass td.status {{ color: #1a7f37; }}
  tr.fail td.status {{ color: #b00; }}
  .reasoning {{ margin-top: 10px; font-size: 12px; color: #444; background: #f6f6f6; border-radius: 6px; padding: 8px 12px; }}
  .reasoning strong {{ display: block; font-size: 11px; text-transform: uppercase; color: #666; margin-bottom: 4px; }}
  .eval-section {{ margin-top: 18px; padding-top: 12px; border-top: 1px dashed #ddd; }}
  .eval-section h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; color: #666; margin: 0 0 6px; }}
  .cost-table {{ margin-top: 24px; font-size: 13px; border-collapse: collapse; width: 100%; }}
  .cost-table td, .cost-table th {{ padding: 6px 10px; border-bottom: 1px solid #eee; text-align: right; }}
  .cost-table th:first-child, .cost-table td:first-child {{ text-align: left; }}
  .cost-table tr.total td {{ font-weight: bold; border-top: 2px solid #333; }}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — narration eval harness (baseline vs. Wikipedia-grounded)</div>

<div class="columns">
  <div class="column">
    <h2>Baseline (names + locations only)</h2>
    <div class="stats">
      <div>narrative generation: {baseline['elapsed_s']:.1f}s</div>
      <div>tokens: in={baseline['input_tokens']} out={baseline['output_tokens']}</div>
      <div>generation cost: ${baseline['cost_usd']:.5f}</div>
      <div>length: {len(baseline['narrative'])} chars</div>
    </div>
    <p>{baseline_html}</p>
    <div class="eval-section">
      <h3>Eval results ({sum(c['passed'] for c in baseline_checks)}/{len(baseline_checks)} passed)</h3>
      <table class="checks">{render_check_rows(baseline_checks)}</table>
      <div class="reasoning"><strong>Judge reasoning ({judge_model})</strong>{baseline_reasoning_html}</div>
    </div>
  </div>

  <div class="column">
    <h2>Wikipedia-grounded</h2>
    <div class="stats">
      <div>wikipedia fetch (5 lookups): {wiki_fetch_elapsed_s:.1f}s</div>
      <div>narrative generation: {grounded['elapsed_s']:.1f}s</div>
      <div>tokens: in={grounded['input_tokens']} out={grounded['output_tokens']}</div>
      <div>generation cost: ${grounded['cost_usd']:.5f}</div>
      <div>length: {len(grounded['narrative'])} chars</div>
    </div>
    <p>{grounded_html}</p>
    <div class="eval-section">
      <h3>Eval results ({sum(c['passed'] for c in grounded_checks)}/{len(grounded_checks)} passed)</h3>
      <table class="checks">{render_check_rows(grounded_checks)}</table>
      <div class="reasoning"><strong>Judge reasoning ({judge_model})</strong>{grounded_reasoning_html}</div>
    </div>
    <div class="sources">
      <h3>Wikipedia sources used</h3>
      <ul>{all_sources_html}</ul>
    </div>
  </div>
</div>

<table class="cost-table">
  <tr><th>Cost group</th><th>Time</th><th>Cost</th></tr>
  <tr><td>Generation (Haiku x2 + wikipedia fetch)</td>
      <td>{baseline['elapsed_s'] + grounded['elapsed_s'] + wiki_fetch_elapsed_s:.1f}s</td>
      <td>${generation_cost:.5f}</td></tr>
  <tr><td>Eval judging ({judge_model} x2)</td>
      <td>{baseline_judge_stats['elapsed_s'] + grounded_judge_stats['elapsed_s']:.1f}s</td>
      <td>${eval_cost:.5f}</td></tr>
  <tr class="total"><td>Total</td>
      <td>{baseline['elapsed_s'] + grounded['elapsed_s'] + wiki_fetch_elapsed_s + baseline_judge_stats['elapsed_s'] + grounded_judge_stats['elapsed_s']:.1f}s</td>
      <td>${total_cost:.5f}</td></tr>
</table>

</body>
</html>"""

    out_path = os.path.join(artifacts_dir, "narration_eval_spike.html")
    with open(out_path, "w") as f:
        f.write(page_html)
    return out_path


if __name__ == "__main__":
    main()
