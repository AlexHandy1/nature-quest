# PLANNING — AI-Personalised Walk Intent: NL Query → GBIF Species Selection

Date: 21 July 2026
Status: Design complete, verified against real GBIF API. Not yet built.
Origin: "AI-personalised walk intent" identified as core differentiator in `WORK_SUMMARY_180726.md`. Designed via `/grill-me` + live API verification in `WORK_SUMMARY_210726.md` (see that file for the full session narrative, including a process issue worth reading before repeating this kind of design session — the initial `/grill-me` pass assumed GBIF API shapes that turned out to be wrong; this plan reflects the corrected, verified design).

This document is meant to be implementable by an agent with no other context from this session. Read this file fully before writing code.

---

## 1. Goal

Prove that a natural-language query (e.g. "Today I want to learn about plants", "show me a mix of birds, plants and mammals", "show me something rare") can be turned into a valid GBIF occurrence query and produce a list of 5 species — the first building block of the "AI-personalised walk intent" feature.

**Scope for this prototype round:** stop at the species list (name, count, kingdom/class, hotspot coordinates). Do **not** wire into waypoint ordering, map generation, or narrative generation — those already exist (`waypoint_spike.py`, `species_narrative_cost_experiment2.py`) and get wired in as a *separate*, later integration step once this piece works in isolation.

**Fixed / out of scope for this round:**
- Location: stays the existing fixed Retiro Park polygon (`GBIF_POLYGON` constant from `waypoint_spike.py`).
- Year: stays fixed (current year, as in existing scripts).
- Species count: fixed at 5.
- Qualitative/descriptive intent (colour, "impressive", "cute", etc.) — GBIF has no such filter; the agent should drop this part of the intent rather than guess at a filter value for it.
- Full raw copy of GBIF's OpenAPI docs — deferred until this round's curated summary proves insufficient.

---

## 2. Verified GBIF API facts (do not re-derive — these were confirmed live against `https://api.gbif.org/v1/` during this session)

### 2.1 `GET /occurrence/search`

Base: `https://api.gbif.org/v1/occurrence/search`. Already used by `waypoint_spike.py` with `geometry`, `year`, `hasCoordinate`, `occurrenceStatus`, `limit`, `offset`.

**Critical fact: there are no string-based taxonomy filters on this endpoint.** Verified via the raw OpenAPI spec (`https://techdocs.gbif.org/openapi/occurrence.json`, linked from `https://techdocs.gbif.org/en/openapi/v1/occurrence`). Taxonomy filters are all `array[integer]` (numeric GBIF backbone keys):

| Param | Type | Meaning |
|---|---|---|
| `kingdomKey` | array[integer] | Kingdom classification key |
| `phylumKey` | array[integer] | Phylum classification key |
| `classKey` | array[integer] | Class classification key |
| `orderKey` | array[integer] | Order classification key |
| `familyKey` | array[integer] | Family classification key |
| `genusKey` | array[integer] | Genus classification key |
| `speciesKey` | array[integer] | Species classification key |
| `taxonKey` | array[integer] | Taxon key from GBIF backbone; includes all descendants (e.g. `taxonKey=212` for Aves matches all birds) |

A string like `kingdom=Plantae` or `class=Aves` sent to this endpoint is **silently ignored** (not an error) — confirmed empirically. Do not use bare rank names as query params here.

Other confirmed relevant params:
- `q` (string) — "Simple full-text search parameter. The value for this parameter can be a simple word or a phrase. Wildcards are not supported." Full-text search over indexed name fields — **not** a semantic/descriptive search (no colour, size, or other qualitative attributes are indexed). Combining `q` with a `*Key` filter is a native AND in one request (no extra engineering needed) — only meaningful when `q` is itself a name-like term (e.g. `q=oak`), not a descriptor.
- `scientificName` (array[string]) — alternative to key-based filtering; not used in this design (we resolve to keys via `species/match` instead, since that also gives us the validation signal we need).
- Filters passed together in one request are ANDed. There is no OR across different filter fields in a single call — this is why mixed-taxa queries need multiple parallel requests (see §4).

### 2.2 `GET /v1/species/match` — name → key resolution

Base: `https://api.gbif.org/v1/species/match`. **Use `/v1/`, not `/v2/species/match`.** (`techdocs.gbif.org`'s own Species API page lists both under GBIF's checklistbank service, which has its own internal versioning; the GBIF API overall is at v1, matching the `api.gbif.org/v1/` base already used everywhere else in this codebase — see `WORK_SUMMARY_210726.md` for the full reasoning trail if this needs re-justifying.)

Params used: `name` (string, required), `rank` (string, one of GBIF's Rank enum values — uppercase, e.g. `KINGDOM`, `CLASS`, `ORDER`, `FAMILY`, `GENUS`).

Response fields (confirmed via live calls, not documented in detail in the OpenAPI spec itself):

```json
{
  "usageKey": 212,
  "scientificName": "Aves",
  "canonicalName": "Aves",
  "rank": "CLASS",
  "status": "ACCEPTED",
  "confidence": 94,
  "matchType": "EXACT",
  "kingdom": "Animalia",
  "phylum": "Chordata",
  "kingdomKey": 1,
  "phylumKey": 44,
  "classKey": 212,
  "class": "Aves"
}
```

- `matchType` is one of `EXACT`, `FUZZY`, `NONE`.
- On `NONE` (no match), most fields are absent — response is just `{"confidence": 100, "matchType": "NONE", "synonym": false}` (verified with a nonsense input).
- On a match, the field named `{rank}Key` (lowercase rank + "Key", e.g. `classKey`, `kingdomKey`) is the numeric value to pass into `occurrence/search`'s corresponding `*Key` param.
- Fuzzy matching corrects typos usefully: `name=Insekta&rank=CLASS` → `matchType: FUZZY, confidence: 85, classKey: 216, class: Insecta`.

**Validation rule (see §5):** accept `EXACT`, or `FUZZY` with `confidence >= 85`; treat everything else (`NONE`, or low-confidence `FUZZY`) as unresolved.

### 2.3 GBIF kingdom values — fully enumerable, verified

All 9 backbone kingdom values, confirmed via live `species/match` calls (all `EXACT`, confidence 96-100):

| Kingdom | `kingdomKey` |
|---|---|
| incertae sedis | 0 |
| Animalia | 1 |
| Archaea | 2 |
| Bacteria | 3 |
| Chromista | 4 |
| Fungi | 5 |
| Plantae | 6 |
| Protozoa | 7 |
| Viruses | 8 |

This set is small and stable enough to hardcode as a local lookup, checked before any live `species/match` call (saves a network round-trip for the single most common case — "plants", "animals", "fungi" intents). Class/order/family/genus are **not** pre-loaded upfront (GBIF has 400+ classes alone) — always resolve those live via `species/match`. A small curated cache for common lay terms (birds→Aves, insects→Insecta, mammals→Mammalia, etc.) may be worth adding *after* this prototype runs and shows which terms actually recur — not built speculatively now.

---

## 3. Structured-output schema (LLM call)

One non-agentic call (plain Anthropic Messages API, tool-use/structured output — not the Agent SDK; see §7 for why), Sonnet 5 to start (see §7).

```json
{
  "taxonFilters": [
    { "taxonRank": "kingdom | phylum | class | order | family | genus", "taxonValue": "<string, as the LLM understands the lay term, e.g. Aves>" }
  ],
  "q": "<string | null — name-like free text only, never a qualitative descriptor>",
  "sort": "most_observed | rarest"
}
```

- `taxonFilters` is a **list** to support mixed-taxa queries (e.g. "birds, plants and mammals" → three entries). Empty list is valid (e.g. "surprise me" / no taxonomic signal → default, no filter).
- `sort` defaults to `most_observed` unless the query clearly implies rarity ("something rare", "unusual", "rarely seen").
- The LLM does **not** produce numeric keys itself — it produces a rank + lay-term string; resolution to the real GBIF key happens in code via §2.2/§2.3, never trusted from the LLM directly (numeric GBIF backbone keys are exactly the kind of detail an LLM would hallucinate confidently and wrongly).
- Prompt must include: the curated GBIF reference doc (§6), explicit instruction that qualitative/descriptive intent (colour, "impressive", etc.) should not produce a `q` value or invented filter — drop it silently rather than guessing.

---

## 4. Query execution & merge strategy

GBIF's `occurrence/search` ANDs all filters in one request and cannot OR across different rank fields — so mixed-taxa queries require **one GBIF call per resolved `taxonFilter`**, run in parallel (not sequential — keep wall time close to the slowest single call).

Per filter group:
1. Resolve `{taxonRank, taxonValue}` → numeric key (§2.2/§2.3, with validation per §5). If unresolved, that group is dropped (see §5's fallback/surfacing behaviour).
2. Call `occurrence/search` with the resolved `*Key` param + fixed params (`geometry`, `year`, `hasCoordinate=true`, `occurrenceStatus=PRESENT`) + `q` if present.
3. Rank that group's results by count (descending for `most_observed`, ascending for `rarest`) — same logic as the existing `select_species()` in `waypoint_spike.py`.

**Species selection across groups — quota/round-robin with graceful degradation:**
- Target: 5 species total, split as evenly as possible across the N resolved groups (e.g. 3 groups → 2/2/1).
- If a group has fewer species available than its quota (including zero), redistribute its unused slots to the other groups rather than returning fewer than 5 total.
- If **all** `taxonFilters` fail to resolve (or none were given), fall back to the existing default behaviour: no filter, `most_observed`, top 5 overall (i.e. exactly today's `waypoint_spike.py` behaviour).
- **A dropped/empty group must be surfaced to the user** (e.g. "no mammals found near here — showing birds and plants instead"), not silently absorbed. Exact surfacing mechanism (CLI print, returned field in the output structure) is an implementation detail — just make sure the information isn't discarded.

---

## 5. Validation / guardrails summary

One uniform mechanism, applied per `taxonFilter`:
1. Check the local kingdom map (§2.3) first if `taxonRank == "kingdom"`.
2. Otherwise (or if not found there), call `species/match?name={taxonValue}&rank={taxonRank uppercase}`.
3. Accept if `matchType == "EXACT"`, or `matchType == "FUZZY" and confidence >= 85`.
4. Otherwise, treat the filter as unresolved → dropped, contributing to the empty-group/redistribution/surfacing behaviour in §4.

`q` is not validated against GBIF (it's passed through as-is) — the guardrail here is entirely in the prompt (§3): instruct the LLM not to populate `q` with non-name-like terms. This is a known, accepted v1 limitation (soft prompt guardrail, not a hard code-level block) — do not over-engineer a content classifier for this.

---

## 6. Reference materials to build (`prototypes/reference/`)

New folder. Two files, to be drafted by the implementing agent and reviewed by the user before use in prompts:

1. **`gbif_docs_summary.md`** — curated, hand-written markdown (not a raw OpenAPI dump) covering:
   - API mechanics actually used: `occurrence/search`'s relevant params (§2.1) and `species/match`'s params/response shape (§2.2), described plainly enough for an LLM prompt.
   - The verified kingdom enum table (§2.3).
   - 10-15 worked lay-term → GBIF rank/value examples, focusing on ambiguous cases (e.g. "trees" → still `kingdom`/`Plantae`, no dedicated "tree" rank; "insects" → `class`/`Insecta`; "birds" → `class`/`Aves`) — this is where LLM hallucination risk is highest.
   - Explicit note that qualitative/descriptive terms have no GBIF equivalent and should be dropped, not guessed.
2. **`gbif_kingdom_keys.json`** (or embedded directly in the script as a constant — implementer's call) — the 9-value static map from §2.3.

Do not include the full raw OpenAPI spec text (§1, out of scope for this round).

---

## 7. Implementation mechanics

- **Non-agentic.** Plain `anthropic.Anthropic().messages.create()` call with tool-use/structured output for the schema (§3) — not the Claude Agent SDK. Rationale: this is a single constrained structured-output generation, not a multi-step agentic task; the 19 July cost experiments (`WORK_SUMMARY_190726.md`) showed non-agentic calls are decisively cheaper and faster for this shape of task, and the reference docs are static content injected into the prompt, not something requiring a live tool-call lookup.
- **Model: Sonnet 5 to start**, not Haiku — explicit decision (user pushed back on assuming structured output is an "easier" task that would suit a smaller model by default). Once this prototype works end-to-end, run the same model-comparison experiment pattern as 19 July (Sonnet vs. Haiku on the 8 test intents in §8) to see if scaling down is viable — do not skip straight to Haiku.
- **GBIF calls run in parallel** where multiple filter groups exist (§4) — use whatever concurrency mechanism fits the rest of the script's style (the existing prototypes are synchronous `requests` calls; consider `concurrent.futures.ThreadPoolExecutor` or similar rather than introducing `asyncio` purely for this, consistent with the 19 July decision that async added no real value for this codebase's call patterns).

### File layout

- `prototypes/scripts/intent_query_spike.py` — new, self-contained (does **not** import from `waypoint_spike.py` or other prototype scripts — established pattern in this codebase, each prototype stays standalone; re-implement the small amount of shared logic like `haversine_m` or the GBIF fetch loop locally if needed).
- `prototypes/scripts/test_intent_query_spike.py` — light TDD (§9), tests for the deterministic logic only.
- `prototypes/reference/gbif_docs_summary.md` — §6.
- `prototypes/reference/gbif_kingdom_keys.json` — §6.

---

## 8. Test intents (manual validation of the LLM step — not automated, since LLM output isn't deterministic)

Run all 8 against the built script and eyeball the output:

1. `"Today I want to learn about plants"` → expect `taxonFilters: [{kingdom, Plantae}]`.
2. `"I'm curious about insects"` → expect `taxonFilters: [{class, Insecta}]`.
3. `"Show me something rare"` → expect `taxonFilters: []`, `sort: rarest`.
4. `"I want to see birds"` → expect `taxonFilters: [{class, Aves}]`.
5. `"Surprise me"` (no strong signal) → expect `taxonFilters: []`, `sort: most_observed` (today's default behaviour, unchanged).
6. `"Show me something colourful"` (qualitative, unmappable) → expect `taxonFilters: []`, no invented `q` value — confirms the qualitative-intent guardrail (§3/§5).
7. A deliberately made-up/misspelled taxon (e.g. a nonsense word) → expect the resolution step to return `matchType: NONE` and the filter dropped — confirms the validation/fallback path (§5) end-to-end, not just the LLM's own output.
8. `"Show me a mix of birds, plants and mammals"` → expect `taxonFilters` with 3 entries (`class/Aves`, `kingdom/Plantae`, `class/Mammalia`), and the final species list should actually contain a mix (confirms §4's quota/round-robin merge works, not just that 3 GBIF calls fired).

**Success criteria:** each case produces a valid, resolved GBIF query (or a correctly-empty one with the right fallback), returns ≥1 species where expected, and cases 5-7 correctly fall back rather than erroring or inventing values. Case 8 specifically must show real mixing (not e.g. 5 birds and 0 mammals/plants) unless Retiro's real data makes a group genuinely empty — in which case the surfacing behaviour (§4) must fire instead of silent failure.

---

## 9. Testing (light TDD — see `WORK_SUMMARY_180726.md`/`WORK_SUMMARY_190726.md` for why prototypes are normally test-free, and this session's `/grill-me` Q9 for why this one differs)

Write tests (`test_intent_query_spike.py`) **only** for the deterministic, non-LLM, non-network logic:
- Kingdom-map lookup (hit and miss cases).
- `species/match` response validation logic (`EXACT` accept, `FUZZY` ≥85 accept, `FUZZY` <85 reject, `NONE` reject) — test against fixed mock response dicts, not live calls.
- Quota/round-robin species selection across groups, including the degradation case (a group with fewer results than its quota, or zero) and redistribution to other groups.

Do **not** test the LLM call itself or make real network calls in the test suite — validated manually via §8 instead.

---

## 10. Open items carried forward (not blocking this prototype, don't solve now)

- Naming decision (Nature Walker vs. Nature Quest) — still open, do not rename files.
- Full end-to-end integration (this species-selection step → waypoint ordering → map/narrative generation) is the next round after this one works in isolation.
- Class/order-level curated key cache — build later from real usage, not upfront.
- Whether `/grill-me` (or a preceding step) should mandate pulling verified third-party API references before an interview starts, to avoid repeating this session's process issue — flagged in `WORK_SUMMARY_210726.md`, not yet actioned.
