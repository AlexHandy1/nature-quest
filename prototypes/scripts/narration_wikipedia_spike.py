#!/usr/bin/env python3
"""
PROTOTYPE — narration_wikipedia_spike.py
Question: does grounding the walk narrative in real Wikipedia extracts (one
per species) make it more accurate and easier to evaluate — i.e. can we point
at a fact in the narrative and trace it back to a specific Wikipedia
sentence? — and how much latency/cost does the extra Wikipedia-fetch step and
longer prompt add versus the baseline (names + locations only) narrative from
narration_tts_spike.py?

Text-generation only, no TTS — narration_tts_spike.py already validated the
narrative -> audio round trip; adding audio here would only add cost/latency
noise to a comparison that's specifically about the text step. Runs both the
baseline and Wikipedia-grounded narrative back to back on the same fixed
sample walk and writes a side-by-side HTML comparison (narrative text, timing,
token/cost breakdown, and — for the Wikipedia variant — a link to each
species' source article) so quality and cost/latency can be eyeballed
together.

Standalone script (this folder's convention: no cross-imports between
prototype scripts), but the Wikipedia resolution logic deliberately mirrors
app/backend/services/wikipedia_client.py's `fetch_species_image` /
`_fetch_summary` — same common-name-first-then-scientific-name-fallback
order, same disambiguation check — rather than inventing a different lookup
path. Production currently fetches this exact summary response for its image
lookup and discards `extract` entirely; this prototype is deliberately built
so that promoting Wikipedia-grounded descriptions later is "also read
`extract` from an already-fetched response," not a second Wikipedia
integration to reconcile against the first.

Uses the plain Anthropic Messages API (not the Agent SDK) — Haiku by default,
same as narration_tts_spike.py. Cost is estimated from token counts (the
plain Messages API has no total_cost_usd field), same MODEL_PRICING table as
species_narrative_cost_experiment2.py.

Throwaway. Do not promote to production.

Requires ANTHROPIC_API_KEY in the environment.

Run: source venv/bin/activate && python prototypes/scripts/narration_wikipedia_spike.py \
    2>&1 | tee prototypes/logs/narration_wikipedia_$(date +%Y%m%d_%H%M%S).log
"""

import os
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

# Same Haiku pricing as species_narrative_cost_experiment2.py's MODEL_PRICING
# table — $/MTok. The plain Messages API has no total_cost_usd field, so this
# is the only cost source here, not a fallback.
HAIKU_PRICING_USD_PER_MTOK = (1.00, 5.00)

WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIPEDIA_USER_AGENT = "nature-quest-prototype/0.1"

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"

# Same fixed sample walk as narration_tts_spike.py's SAMPLE_WALK — kept in
# sync deliberately (both are the Retiro Park 5-species set from
# species_narrative_spike.py's HARDCODED_SPECIES) so narrative comparisons
# across both scripts stay apples-to-apples. Duplicated, not imported, per
# this folder's standalone-scripts convention.
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
# fetch_species_image()/_fetch_summary(): common name first (more likely to
# match the article title a human would search for), scientific name
# fallback on a missing/disambiguation article. Same order, same
# disambiguation check — only difference is this also keeps `extract` and
# the article URL, which production's version discards. ─────────────────

def fetch_wikipedia_summary(title):
    url = f"{WIKIPEDIA_SUMMARY_URL}/{quote(title)}"
    try:
        response = requests.get(
            url, timeout=15, headers={"User-Agent": WIKIPEDIA_USER_AGENT}
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None

    data = response.json()
    if data.get("type") == "disambiguation" or not data.get("extract"):
        return None

    page_url = data.get("content_urls", {}).get("desktop", {}).get("page")
    return {
        "title": data.get("title"),
        "extract": data.get("extract"),
        "url": page_url,
    }


def fetch_species_wikipedia_extracts(walk):
    extracts = {}
    start = time.monotonic()
    for sp in walk["species"]:
        summary = fetch_wikipedia_summary(sp["common_name"])
        if summary is None:
            summary = fetch_wikipedia_summary(sp["scientific_name"])
        extracts[sp["scientific_name"]] = summary
        status = f"{GREEN}found{RESET}" if summary else f"{RED}not found{RESET}"
        print(f"  {sp['common_name']} ({sp['scientific_name']}): {status}")
    elapsed_s = time.monotonic() - start
    return extracts, elapsed_s


# ── Narrative generation ─────────────────────────────────────────────────

LOCATION_GUIDANCE = """Infer where in the world this walk is taking place from the coordinates and the
species themselves — that's not a species fact, so you may use your own
knowledge for it. Don't state or imply a time of day, season, or "today" —
you have no way of knowing when the walk is actually happening."""

BASELINE_FACTS_GUIDANCE = """You have only each species' name and location to work from — draw on your own
knowledge of these species, and weave in a sense of place and journey."""

GROUNDED_FACTS_GUIDANCE = """Any fact you state about a species (behaviour, appearance, range, diet, etc.)
must come only from that species' Wikipedia extract above — never invent or
guess beyond what the extract says, and never repeat its technical or
taxonomic wording verbatim (e.g. "endemic," "Holarctic," "ornamental
introduction," "monochrome plumage") — translate it into plain, warm
language a curious visitor with no biology background would enjoy. It's good
to include a fact or reference from each extract where it fits naturally."""


def build_narrative_prompt(walk, extracts=None):
    """One prompt builder for both variants. Pass extracts=None for the
    baseline (names + locations only); pass the dict from
    fetch_species_wikipedia_extracts() to ground the narrative in Wikipedia
    text. Only the per-species Wikipedia-extract lines and the guidance
    paragraph differ between the two — everything else is shared, so it's
    written once rather than duplicated across two near-identical prompts."""
    lines = []
    for i, sp in enumerate(walk["species"], 1):
        line = (
            f"{i}. {sp['common_name']} ({sp['scientific_name']}) at "
            f"({sp['lat']:.4f}, {sp['lon']:.4f})"
        )
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

Write a single flowing narrative guide of roughly 120-160 words (about 45-60
seconds spoken aloud) for a walker following this route. Write continuous
narrated prose, not a list. Do not use markdown, and do not include a title
or heading — start straight into the narration."""


def estimated_cost_usd(input_tokens, output_tokens):
    in_price, out_price = HAIKU_PRICING_USD_PER_MTOK
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


def generate_narrative(prompt, client, label):
    print(f"\n  {DIM}--- {label} PROMPT ---{RESET}")
    print(f"  {DIM}{prompt}{RESET}")

    start = time.monotonic()
    response = client.messages.create(
        model=NARRATIVE_MODEL,
        max_tokens=NARRATIVE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_s = time.monotonic() - start

    narrative = "".join(block.text for block in response.content if block.type == "text").strip()
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost_usd = estimated_cost_usd(input_tokens, output_tokens)

    print(f"\n  {DIM}--- {label} RESPONSE ({elapsed_s:.1f}s) ---{RESET}")
    print(f"  {narrative}")
    print(
        f"  {DIM}[in={input_tokens} out={output_tokens} "
        f"cost=${cost_usd:.5f} model={NARRATIVE_MODEL}]{RESET}"
    )

    return {
        "narrative": narrative,
        "elapsed_s": elapsed_s,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }


def main():
    print(f"\n{BOLD}PROTOTYPE: Baseline vs. Wikipedia-grounded narrative spike{RESET}")
    print(f"{DIM}Question: does grounding in Wikipedia extracts improve accuracy/")
    print(f"testability, and what does it cost in latency + $?{RESET}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print(f"\n{RED}ANTHROPIC_API_KEY is not set.{RESET}")
        sys.exit(1)

    client = Anthropic(api_key=anthropic_key)

    header("STEP 1: Fetch Wikipedia extracts")
    extracts, wiki_fetch_elapsed_s = fetch_species_wikipedia_extracts(SAMPLE_WALK)
    print(f"  {DIM}total fetch time: {wiki_fetch_elapsed_s:.1f}s{RESET}")

    header("STEP 2a: Baseline narrative (names + locations only)")
    baseline_prompt = build_narrative_prompt(SAMPLE_WALK)
    baseline = generate_narrative(baseline_prompt, client, "BASELINE")

    header("STEP 2b: Wikipedia-grounded narrative")
    grounded_prompt = build_narrative_prompt(SAMPLE_WALK, extracts)
    grounded = generate_narrative(grounded_prompt, client, "WIKIPEDIA-GROUNDED")

    header("SUMMARY — cost & timing")
    grounded_total_s = wiki_fetch_elapsed_s + grounded["elapsed_s"]
    print(f"  {'Step':<28} {'Time':>8} {'Cost':>10}")
    print(f"  {'baseline narrative':<28} {baseline['elapsed_s']:>7.1f}s {'$' + format(baseline['cost_usd'], '.5f'):>10}")
    print(f"  {'wikipedia fetch (5 lookups)':<28} {wiki_fetch_elapsed_s:>7.1f}s {'-':>10}")
    print(f"  {'grounded narrative':<28} {grounded['elapsed_s']:>7.1f}s {'$' + format(grounded['cost_usd'], '.5f'):>10}")
    print(f"  {'wikipedia total':<28} {grounded_total_s:>7.1f}s {'$' + format(grounded['cost_usd'], '.5f'):>10}")
    print(f"  {'-' * 48}")
    added_time_s = grounded_total_s - baseline["elapsed_s"]
    added_cost_usd = grounded["cost_usd"] - baseline["cost_usd"]
    print(f"  {'ADDED by Wikipedia grounding':<28} {added_time_s:>+7.1f}s {'$' + format(added_cost_usd, '+.5f'):>10}")

    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    html_path = generate_comparison_page(
        artifacts_dir, baseline, grounded, extracts, wiki_fetch_elapsed_s, SAMPLE_WALK
    )
    print(f"\n{GREEN}Opening comparison -> {html_path}{RESET}\n")
    webbrowser.open(f"file://{html_path}")


def generate_comparison_page(artifacts_dir, baseline, grounded, extracts, wiki_fetch_elapsed_s, walk):
    grounded_total_s = wiki_fetch_elapsed_s + grounded["elapsed_s"]
    added_time_s = grounded_total_s - baseline["elapsed_s"]
    added_cost_usd = grounded["cost_usd"] - baseline["cost_usd"]

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

    baseline_html = baseline["narrative"].replace("\n\n", "</p><p>").replace("\n", " ")
    grounded_html = grounded["narrative"].replace("\n\n", "</p><p>").replace("\n", " ")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>PROTOTYPE — Wikipedia-grounded narrative spike</title>
<style>
  body {{ font-family: sans-serif; max-width: 1100px; margin: 40px auto; padding: 0 20px; color: #222; }}
  #banner {{ display: inline-block; background: #ffd700; padding: 3px 12px; border-radius: 4px;
    font-size: 11px; font-weight: bold; margin-bottom: 16px; }}
  .columns {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .column {{ flex: 1; min-width: 320px; border: 1px solid #ddd; border-radius: 8px; padding: 16px 20px; }}
  .column h2 {{ font-size: 15px; margin-top: 0; }}
  .stats {{ font-size: 12px; color: #555; background: #f6f6f6; border-radius: 6px; padding: 8px 12px; margin-bottom: 14px; }}
  .stats div {{ margin: 2px 0; }}
  p {{ line-height: 1.6; }}
  .sources {{ margin-top: 18px; padding-top: 12px; border-top: 1px dashed #ddd; }}
  .sources h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; color: #666; margin: 0 0 6px; }}
  .sources ul {{ padding-left: 20px; margin: 0; font-size: 12px; }}
  .wiki-title {{ color: #666; font-size: 12px; }}
  .missing {{ color: #b00; font-size: 12px; }}
  .delta {{ margin-top: 24px; font-size: 13px; background: #eef6ff; border-radius: 6px; padding: 10px 14px; }}
  .delta strong {{ color: #1a5fb4; }}
</style>
</head>
<body>
<div id="banner">PROTOTYPE — baseline vs. Wikipedia-grounded narrative</div>

<div class="columns">
  <div class="column">
    <h2>Baseline (names + locations only)</h2>
    <div class="stats">
      <div>narrative generation: {baseline['elapsed_s']:.1f}s</div>
      <div>tokens: in={baseline['input_tokens']} out={baseline['output_tokens']}</div>
      <div>estimated cost: ${baseline['cost_usd']:.5f}</div>
      <div>length: {len(baseline['narrative'])} chars</div>
    </div>
    <p>{baseline_html}</p>
  </div>

  <div class="column">
    <h2>Wikipedia-grounded</h2>
    <div class="stats">
      <div>wikipedia fetch (5 lookups): {wiki_fetch_elapsed_s:.1f}s</div>
      <div>narrative generation: {grounded['elapsed_s']:.1f}s</div>
      <div>total: {grounded_total_s:.1f}s</div>
      <div>tokens: in={grounded['input_tokens']} out={grounded['output_tokens']}</div>
      <div>estimated cost: ${grounded['cost_usd']:.5f}</div>
      <div>length: {len(grounded['narrative'])} chars</div>
    </div>
    <p>{grounded_html}</p>
    <div class="sources">
      <h3>Wikipedia sources used</h3>
      <ul>
        {all_sources_html}
      </ul>
    </div>
  </div>
</div>

<div class="delta">
  <strong>Added by Wikipedia grounding:</strong> {added_time_s:+.1f}s, ${added_cost_usd:+.5f}
</div>

</body>
</html>"""

    out_path = os.path.join(artifacts_dir, "narration_wikipedia_spike.html")
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    main()
