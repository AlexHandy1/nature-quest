# nature-walker — Initial Plan
**Created:** 25 June 2026
**Working name:** `nature-walker`

---

## What it is

A tool that turns public GBIF biodiversity observation data into compelling, navigable nature walks. Given a location, it selects the most interesting species currently present, designs a walk route that intersects their recorded ranges, and generates LLM-written field notes grounded in real observation data and Wikipedia natural history content.

**The question it answers:** *"How can I find interesting nature near me, and get out and experience it?"*

**First user interaction:** Enter a location (or allow GPS) → press one button → receive a walk with 5 species to look for, each with a field note, on a map. No account, no onboarding. The Retiro Park, Madrid example is pre-generated on the landing page so users immediately understand the product.

---

## Why build this

- **No direct equivalent exists:** iNaturalist (observation logging), AllTrails (routes), GBIF Explorer (raw data) — none combine walk design + species intelligence + LLM narrative
- **Solves a real problem:** most people don't know what's interesting to look for on a walk, or where to look for it
- **Domain advantage:** NatureMetrics ecology background means the species selection and field note quality can be grounded in real natural history knowledge
- **Good technical scope:** full-stack (FastAPI + React/TypeScript + Mapbox) with a meaningful AI layer (agentic tool-use, eval framework) — not a toy project, not an over-engineered one

---

## Data Sources

| Source | What it provides | Notes |
|---|---|---|
| GBIF API (`api.gbif.org/v1/occurrence/search`) | Historical occurrence records: lat/lon, date, taxon, quality grade, spatial radius queries, phenology (month-by-month counts) | Free, no auth, well-documented. iNaturalist data is aggregated here — no separate iNat API needed |
| Wikipedia API | Species natural history summaries: habitat, behaviour, diet, appearance, conservation status | Free, no auth. Injected into agent context per species |
| OpenStreetMap / Overpass API | Footpaths, trails, walk routes near a location | **Needs spiking** — unknown complexity. Fall back to markers-only if OSM proves complex for MVP |

---

## Core Architecture

### Species selection pipeline

Given a location and walk duration:

1. Query GBIF for all occurrence records within N metres of the location, current month ± 1, quality-graded observations only
2. Score each candidate species:

```
score = 0.4 × seasonality + 0.4 × recency + 0.2 × spottability
```

- **Seasonality** — % of historical GBIF records for this species at this location that fall in the current month
- **Recency** — decay function: seen past 7 days = 1.0, past 30 days = 0.5, past 90 days = 0.1, older = 0
- **Spottability** — normalised log(total observation count in this grid square)
- **Taxa multiplier applied after:** birds ×1.2, mammals ×1.15, plants ×1.0, insects ×0.9 (adjustable parameter)

3. Select 5 species with enforced taxa mix:
   - Slot 1: top-scoring bird
   - Slot 2: top-scoring plant
   - Slot 3: wildcard (highest overall score regardless of group)
   - Slots 4–5: next highest overall scores

### Walk route design

**MVP approach:** Find a pleasant OSM walking path/loop in the area → select species whose GBIF observation records fall within N metres of that path.

- Route is not derived from species locations (that's a follow-up complexity)
- Route first, species selected based on overlap
- OSM/Overpass API for path data — **spike against Retiro first** to assess complexity
- If OSM routing proves complex: fall back to species markers on map, user self-navigates
- Hard constraint: walk must be completable within user's time budget

### Agent architecture

**Single agent per walk request** using Claude Sonnet 4.6 via the Claude Agent SDK.

For each of the 5 selected species, the agent:
1. Calls `get_gbif_details(species, location, month)` — observation count, date range, coordinates
2. Calls `get_wikipedia_summary(species)` — natural history content
3. Writes a 2–3 sentence field note grounded in both sources

Parallel tool calls for all 5 species in one agent turn.

**Model-agnostic seam built from day one:** Agent logic lives in a `NatureAgent` class with model client injected as a dependency. Claude is the first implementation. Open-source alternative (e.g. locally-run model via Ollama or similar — needs research) is a second implementation behind the same interface. No over-engineered abstraction — just a clean seam.

```python
class NatureAgent:
    def __init__(self, model_client):  # client is swappable
        self.client = model_client

    def generate_field_note(self, species, gbif_context, wiki_context) -> str:
        ...
```

---

## Eval Framework

**Primary harness: Retiro Park, Madrid.**
A well-documented, data-rich location that can be physically verified, and serves as the pre-generated landing page example.

**Three eval dimensions:**

| Dimension | Method | Automation |
|---|---|---|
| Factual accuracy | Is the species actually recorded at this location in this season? | Programmatic — cross-check against GBIF phenology data |
| Grounding | Does every specific claim trace to a GBIF record or Wikipedia source? | LLM-as-judge scoring against retrieved source text |
| Usefulness | Would this field note help you actually spot the species? | Manual — gold standard field guides as reference |

**Test suite:** Generate field notes for 10 Retiro species. Score all three dimensions. Establish baseline. Every subsequent model or prompt change runs against the same 10.

**Approach:** Prototype and spike first, then formalise eval. Manual gold standard before automation. Evals evolve with the system.

---

## Feature Roadmap

### MVP (spike + first working version)
- [ ] GBIF data pipeline: query by location, month, quality grade
- [ ] Species scoring and selection algorithm (5 species, taxa mix)
- [ ] Single agent: GBIF + Wikipedia tool calls → field note per species
- [ ] Pre-generated Retiro walk: cached, served as landing page
- [ ] Minimal map: Mapbox with species markers (route overlay if OSM spike succeeds)
- [ ] Single API endpoint: `POST /walk` → walk object (5 species cards + map data)
- [ ] Basic React frontend: map + species cards, clean but not polished

### Layer 2 — Core experience
- [ ] Eye-spy tick-off: target species list per walk, tap-to-confirm UI
- [ ] Nature forecast: probabilistic encounter estimate with confidence tier display
- [ ] OSM route integration (if spike confirms feasibility)
- [ ] Location input for arbitrary places (beyond Retiro)
- [ ] Formalised eval pipeline with automated scoring

### Layer 3 — Game feel and engagement
- [ ] Pokémon Go aesthetic: animated species markers, encounter reveal screen, rarity tiers (colour-coded from GBIF observation frequency)
- [ ] Walk completion screen: species haul, rarity score, new finds
- [ ] Species "dex": persistent record of every species ever ticked off, empty silhouettes for unseen species
- [ ] Seasonal micro-challenges: push notifications for phenology-driven goals
- [ ] Nature Top Trumps: species cards with live GBIF stats, plausibility scoring on claimed sightings

### Layer 4 — Advanced AI and multi-modal
- [ ] Audio narration: location-triggered TTS playback as you walk (GPS geofence → pre-rendered audio per waypoint)
- [ ] Progressive nature eye: adaptive species difficulty based on observation history (sommelier model)
- [ ] Phenology deviation alerts: compare current iNaturalist data to GBIF baseline, surface climate signals
- [ ] Citizen science / fill the gap mode: surface taxonomic data gaps, design walks to address them
- [ ] Nature on your commute: detour finder for existing journeys
- [ ] Open-source model alternative: second NatureAgent implementation, swappable at runtime

---

## Build Sequence (immediate next steps)

### Sprint 0 — Data spike (before any frontend or agent work)
**Goal:** confirm GBIF data for Retiro produces clean, interesting, seasonally-appropriate species.

1. Hit `api.gbif.org/v1/occurrence/search` with Retiro lat/lon, current month, quality-graded
2. Inspect raw results: what species, what observation counts, what date distribution
3. Implement scoring function, run against Retiro results, inspect top 5
4. Spike OSM/Overpass for Retiro: can you get a pleasant walking loop easily?
5. Decision point: adopt OSM routing or proceed with markers-only

**Spike answers the question:** does GBIF data for Retiro produce 5 genuinely interesting, seasonally-appropriate species right now?

### Sprint 1 — Working backend
- FastAPI project scaffold, virtual environment, test setup
- GBIF query module with scoring and species selection
- NatureAgent class with Claude SDK, GBIF + Wikipedia tool calls
- `POST /walk` endpoint returning walk object
- Pre-generate and cache Retiro walk

### Sprint 2 — Minimal frontend + pre-generated demo
- React/TS scaffold
- Mapbox map with species markers
- Species cards with field notes
- Retiro pre-generated example as landing page

### Sprint 3 — Eval harness
- 10-species Retiro test suite
- Factual accuracy checker (programmatic, GBIF cross-check)
- LLM-as-judge grounding scorer
- Baseline measurement, iterate on prompts

---

## Key Decisions Log

| Decision | Choice | Why |
|---|---|---|
| Platform | Web app first (PWA later) | Iterate fast on AI/backend; frontend complexity is not the core challenge |
| Data source | GBIF only | iNaturalist data is aggregated into GBIF; one clean source beats two |
| Agent architecture | Single agent, parallel tool calls | Simpler than multi-agent for MVP; Claude SDK, swappable seam for OSS alternative |
| Model | Claude Sonnet 4.6 | Fast, cheap enough for prototyping, strong tool-use; upgrade path to Opus if needed |
| LLM context | GBIF metadata + Wikipedia summary | GBIF = grounding facts; Wikipedia = natural history content. iNat observation notes are noisy — skip for now |
| RAG vs agentic | Agentic tool-use | GBIF and Wikipedia are structured queryable APIs, not document corpora — vector embeddings solve the wrong problem |
| Route design | OSM path → species overlap (not hotspot routing) | Simpler for MVP; hotspot routing (derive route from species data) is a clear follow-up |
| Eval harness | Retiro Park as ground truth | Known location, physically verifiable, serves as demo and test suite |
| Species count | 5 per walk | Achievable in one walk, enough for discovery feel |
| Taxa mix | Enforced: 1 bird + 1 plant + 1 wildcard + 2 open | Guarantees variety; prevents one taxon dominating on raw score |

---

## Risks

| Risk | Mitigation |
|---|---|
| GBIF data sparse for some locations | Show confidence tier; degrade gracefully; Retiro is data-rich so MVP is safe |
| OSM routing complex to implement well | Markers-only fallback; spike first before committing |
| LLM ecological hallucinations | Agentic grounding (tool calls provide cited facts); eval harness catches regressions |
| Open-source model quality gap vs Claude | Architecture seam means Claude always available as fallback; OSS is optional mode |
| Project scope expands before core works | Strict layer ordering — don't touch Layer 2 until Layer 1 is working and evaluated |
