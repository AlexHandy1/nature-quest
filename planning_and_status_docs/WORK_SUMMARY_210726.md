# Work Summary — 21 July 2026

## Decision

**FOR NOW**, go with the Zelda-style map treatment, based off the OpenTopoMap non-vector stylised version in `prototypes/ux/map_narrative_layout_prototype.html` (Variant A) — not the vector re-skin in `map_narrative_layout_prototype_v2.html`.

**Why:** interim direction while the WebGL 3D map exploration (added to `FEATURE_IDEAS_BACKLOG.md` this session) is investigated as the longer-term map experience.

## Backlog addition

Added "WebGL 3D map experience" to `planning_and_status_docs/FEATURE_IDEAS_BACKLOG.md` — explore WebGL for a genuinely 3D, game-like map (vs. the current 2D Leaflet prototypes), aimed at the fantasy video game journey direction.

## Next steps

1. Investigate WebGL as the 3D map direction.
2. In the meantime, treat v1's Zelda variant (OpenTopoMap + CSS filter) as the reference map style rather than continuing to iterate the v2 vector re-skin.

---

## Session 2 — AI-personalised walk intent: `/grill-me` design session + process issue

### What was explored

Ran `/grill-me` on the "AI-personalised walk intent" feature (NL query → GBIF query → 5 species), per `WORK_SUMMARY_180726.md`'s original direction. Worked through scope, schema shape, agentic-vs-not, validation/guardrails, mixed-taxa handling, docs reference approach, TDD stance, model choice, and file layout — full design captured in conversation, not yet written to a PLANNING doc (see Next steps).

### Process issue surfaced

**The `/grill-me` session designed a schema against assumed GBIF API behaviour instead of the verified API spec.** The design assumed `/occurrence/search` accepts string taxonomy filters (e.g. `kingdom=Plantae`, `class=Aves`). When starting to build the local GBIF reference docs (the planned `prototypes/reference/` component), pulling the actual OpenAPI spec (`https://techdocs.gbif.org/openapi/occurrence.json`) showed this is wrong: the endpoint only accepts **numeric** `*Key` params (`kingdomKey`, `classKey`, `orderKey`, `familyKey`, `genusKey`, `speciesKey`, `taxonKey`) — there is no string-based taxon filter at all. A string value like `class=Aves` is silently ignored by GBIF rather than erroring, so this would have failed silently rather than loudly if built as originally designed.

**Root cause:** the design session proceeded from remembered/assumed API shape rather than the verified spec. `techdocs.gbif.org` pages are JS-rendered (Redoc), so a plain `WebFetch` only returns nav chrome, not parameter tables — this made it easy to skip verification. The reliable path, used once this was caught: `agent-browser` to load the rendered docs page and find the linked raw OpenAPI JSON (e.g. `/openapi/occurrence.json`, linked from the page), then read the spec directly with a script — verbatim, low-token, and authoritative.

**Consequence:** the design decisions from the `/grill-me` session (schema shape, validation/guardrail approach) need revisiting once the real API surface — including how a name (e.g. "Aves") resolves to the numeric key GBIF requires — is confirmed. Not yet resolved as of this entry.

**For future sessions:** consider whether `/grill-me` (or a step before it) should require pulling verified API references for any third-party integration *before* the interview starts, rather than discovering gaps mid-build. Worth a look next time `/grill-me` or a similar planning skill is customised.

### Resolution mechanism confirmed

Verified `GET https://api.gbif.org/v1/species/match?name={value}&rank={RANK}` (not the `/v2/species/match` initially considered — `v1` is the correct choice, consistent with the stable `api.gbif.org/v1/` base already used by every existing prototype script, including `/v1/occurrence/search`; the "v2" naming inside GBIF's checklistbank OpenAPI spec is that service's own internal versioning, unrelated to the main GBIF API's v1 designation described on the techdocs landing page). Confirmed live via direct calls against the real endpoint:

- `?name=Aves` → `{matchType: EXACT, confidence: 94, classKey: 212, class: Aves, ...}` — `classKey: 212` matches the worked `taxonKey=212` example used throughout the `occurrence/search` docs for Aves, cross-confirming correctness.
- `?name=Plantae&rank=KINGDOM` → `{matchType: EXACT, confidence: 96, kingdomKey: 6}`.
- `?name=Insekta&rank=CLASS` (deliberate misspelling) → `{matchType: FUZZY, confidence: 85, classKey: 216, class: Insecta}` — fuzzy-corrects the typo.
- `?name=Zzznotarealtaxon` → `{matchType: NONE, confidence: 100}` — clean no-match signal.

**All 9 GBIF kingdom values verified as EXACT matches** (`Animalia:1, Archaea:2, Bacteria:3, Chromista:4, Fungi:5, Plantae:6, Protozoa:7, Viruses:8, incertae sedis:0`) — small, fully enumerable, stable.

### Design revised

Corrected pipeline (supersedes the `/grill-me` session's schema/validation assumptions, everything else from that session — mixed-taxa handling, model choice, TDD scope, file layout — still holds):

1. LLM (Sonnet 5, non-agentic structured output) → `{taxonFilters: [{taxonRank, taxonValue}], q, sort}`.
2. For each `taxonFilter`: check the local kingdom-key map first (see below); if no match, call `GET /v1/species/match?name={taxonValue}&rank={taxonRank}`.
3. **Validation** (replaces the old kingdom-whitelist + partial species-match idea — one uniform mechanism for every rank now): accept `EXACT` matches, or `FUZZY` above a confidence threshold (e.g. 85, per the "Insekta"→"Insecta" case above); `NONE` or low-confidence fuzzy matches are dropped — this is the "empty group" case agreed in the `/grill-me` session (surfaced to the user, slots redistributed).
4. Take the resolved `{rank}Key` (e.g. `classKey: 216`) and run one `occurrence/search` call per surviving filter, in parallel, alongside the existing fixed params (polygon, year, `hasCoordinate`, `occurrenceStatus`).
5. `q` (free-text) stays a direct pass-through string param on `occurrence/search` (no resolution needed).
6. Merge species across groups via quota/round-robin, as agreed.

**Pre-loaded kingdom-key cache — decided.** A static local map (9 verified values) checked before any live call, since the kingdom rank is small and fully enumerable. Class/order/family/genus are *not* pre-loaded upfront (too large a space, ~400+ classes alone) — instead build a small curated cache incrementally from real usage/test-intent results later, not guessed ahead of time. The live `species/match` call remains the fallback path regardless of cache coverage.

### Next steps

1. Draft `prototypes/reference/` — GBIF docs summary markdown (occurrence + species match mechanics, verified param names/types, worked lay-term examples) and the kingdom-key static map — for user review before use.
2. Write up the confirmed design as a PLANNING doc, per `/documentation-and-adrs`.
3. Begin light-TDD build of the deterministic logic (validation, quota/round-robin selection, sparse-group degradation) per the `/grill-me` session's Q9 decision.
