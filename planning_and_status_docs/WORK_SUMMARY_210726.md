# Work Summary — 21 July 2026

## Decision

**FOR NOW**, go with the adventure-style map treatment, based off the OpenTopoMap non-vector stylised version in `prototypes/ux/map_narrative_layout_prototype.html` (Variant A) — not the vector re-skin in `map_narrative_layout_prototype_v2.html`.

**Why:** interim direction while the WebGL 3D map exploration (added to `FEATURE_IDEAS_BACKLOG.md` this session) is investigated as the longer-term map experience.

## Backlog addition

Added "WebGL 3D map experience" to `planning_and_status_docs/FEATURE_IDEAS_BACKLOG.md` — explore WebGL for a genuinely 3D, game-like map (vs. the current 2D Leaflet prototypes), aimed at the fantasy video game journey direction.

## Next steps

1. Investigate WebGL as the 3D map direction.
2. In the meantime, treat v1's adventure-style variant (OpenTopoMap + CSS filter) as the reference map style rather than continuing to iterate the v2 vector re-skin.

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

### Next steps (superseded by Session 3 below — all completed this session)

1. ~~Draft `prototypes/reference/`~~ — done, see Session 3.
2. ~~Write up the confirmed design as a PLANNING doc~~ — done, `PLANNING_INTENT_QUERY_210726.md`.
3. ~~Begin light-TDD build of the deterministic logic~~ — done, see Session 3.

---

## Session 3 — PLANNING doc, reptile/fish resolution, TDD build, live validation

### What was built

- `planning_and_status_docs/PLANNING_INTENT_QUERY_210726.md` — full step-by-step technical plan (verified GBIF facts, structured-output schema, query execution/merge strategy, validation rules, reference-doc contents, file layout, 10 test intents, TDD scope), written to be implementable by an agent with no other session context.
- `prototypes/reference/gbif_kingdom_keys.json` — 9 verified kingdom values.
- `prototypes/reference/gbif_common_class_keys.json` — 10 values: 6 common Animalia classes (Aves 212, Insecta 216, Mammalia 359, Amphibia 131, Arachnida 367, Gastropoda 225) + 4 reptile classes (see below).
- `prototypes/reference/gbif_common_order_keys.json` — 5 fish orders (Perciformes 587, Cypriniformes 1153, Scorpaeniformes 590, Gadiformes 549, Clupeiformes 538).
- `prototypes/reference/gbif_docs_summary.md` — curated prompt reference (endpoint mechanics, kingdom/class/order tables, unranked-clade-name warning with the `Tetrapoda`→arachnid cautionary example, 17 worked lay-term examples, reptile/fish handling).
- `prototypes/scripts/intent_query_spike.py` — full pipeline: non-agentic Sonnet 5 structured-output call (tool-use, forced `tool_choice`) → per-filter taxon resolution (local cache first, then live `species/match`) → parallel `occurrence/search` calls (`ThreadPoolExecutor`) → quota/round-robin merge → result printout. Full prompt/response/raw-usage logging and a time/cost summary table, matching the style of `species_narrative_cost_experiment2.py` (added after an explicit request — the first pass under-logged compared to the existing prototypes).
- `prototypes/scripts/test_intent_query_spike.py` — 13 passing tests (light TDD, tracer-bullet style) covering the three deterministic behaviours: local cache lookup (hit/miss), `species/match` response validation (`EXACT`/`FUZZY`≥85/`FUZZY`<85/`NONE`/`HIGHERRANK`), and quota/round-robin species selection (even split, uneven split, single-group and full-group degradation, insufficient-total-supply).
- `prototypes/requirements.txt` — added `pytest` (installed into the existing venv).

### What was explored / learnt

- **Reptiles resolved as a 4-class union, not left unresolved.** `species/match?name=Reptilia&rank=CLASS` returns `matchType: HIGHERRANK` (falls back to phylum `Chordata`). Fetching the full record for the `CLASS`-ranked `Reptilia` entry (`usageKey: 12170551`) revealed its own `remarks` field: *"In the 2022 Catalogue of Life checklist, the previous class Reptilia has been retired as a paraphyletic group... GBIF continues to include it as a pro parte synonym for Crocodylia, Squamata, Testudines and Sphenodontia."* Confirmed via `GET /v1/species/44/children` (Chordata's full 7,508-entry child list, paginated) that all four are real, `ACCEPTED`, `CLASS`-ranked backbone entries. A "reptiles" request now becomes 4 parallel `class` filters, the same mechanism as any mixed-taxa request.
- **Fish resolved as a data-driven, coverage-based holding solution, not a taxonomic fix.** The same Chordata-children listing showed ray-finned fish (most fish species) isn't a class in the current backbone at all — only ~46 separate orders, plus 6 unrelated classes (sharks, lampreys, etc.), 52 fish-related groups total, no small complete union like reptiles had. Ranked all 52 by real global GBIF occurrence count (`occurrence/search?{classKey|orderKey}=X&limit=0`); the top 5 orders (`Perciformes` 32.1M, `Cypriniformes` 11.8M, `Scorpaeniformes` 8.4M, `Gadiformes` 8.2M, `Clupeiformes` 7.9M) cover ~67% of all fish-related observations. Explicitly flagged as a holding solution, not exhaustive.
- **Confirmed `q=fish` is a worse substitute than the taxonomic filter, with real evidence.** In the Retiro polygon, `q=fish` returned only 4 records (2× `Cyprinus carpio`, 1× `Lepomis gibbosus`, and a **false positive**: `Ardea cinerea`, a grey heron — matched on incidental indexed text, not taxonomy). The real `orderKey=1153` (Cypriniformes) filter returned 73 genuine carp records in the same area — `q=fish` missed 69 of them. Confirms `q` is name/text search only, never a category/semantic filter — consistent with the design's existing framing, now with concrete supporting data.
- **`GET /v1/species/{usageKey}` and `GET /v1/species/{key}/children`** are useful, previously-unused endpoints for this kind of investigation — the former gives a taxon's full record including `accepted`/`acceptedKey`/`remarks` for synonyms, the latter lists a taxon's direct backbone children (used to enumerate all of Chordata's classes/orders directly rather than guessing candidate names one at a time).
- **Live end-to-end validation, real GBIF + real Sonnet 5 calls, 3 of the plan's 10 test intents run:**
  - *"Today I want to learn about plants"* → `{kingdom, Plantae}`, resolved via local cache, 58 occurrences → 36 species → 5 real Retiro plants (daisy, garlic, almond, celandine, stone pine). $0.0107, 3.4s total.
  - *"Show me a mix of birds, plants and mammals"* → all 3 filters resolved via local cache; 3 parallel GBIF calls in 4.0s; genuine mix returned (2 birds, 2 plants, 1 mammal) because Retiro's mammal data is sparse (only 2 species with any occurrences) — quota/degradation logic handled it correctly with no manual intervention. $0.0112, 7.0s total.
  - *"I want to see fish and reptiles on my walk"* → 9 filters (5 fish + 4 reptile), all resolved via local cache, 9 parallel GBIF calls in 1.1s; 7 of 9 groups genuinely empty in Retiro, correctly surfaced (*"No species found for: Perciformes, Scorpaeniformes, Gadiformes, Clupeiformes, Crocodylia, Squamata, Sphenodontia"*); the 2 non-empty groups produced a real result — `Cyprinus carpio` (carp) plus 4 real turtle species (`Trachemys scripta`, `Pseudemys nelsoni`, `Graptemys pseudogeographica`, `Pseudemys concinna`) — a plausible match to Retiro's lake's known population of released pet turtles. $0.0129, 4.1s total.
- **Minor process note, unresolved:** partway through the session, Write/Edit tool calls stopped producing visible approval prompts even though the user's permission mode indicator showed "manual mode on." Checked project-level (`.claude/settings.json`/`settings.local.json` — neither exists) and user-level (`~/.claude/settings.json` — no permissions/allowlist rules present) config; nothing on disk explains it. Most likely a session-level "always allow" grant from an earlier prompt, which isn't visible or persisted anywhere inspectable from this session. Not resolved — flagged for the user to check if it recurs.

### Decisions and trade-offs

- **Decision:** solve reptiles now (4-class union) but only produce a data-driven partial fix for fish (5-order holding solution), not attempt full taxonomic completeness for either. **Why:** reptiles had a small, clean, complete answer once investigated (GBIF's own data explains the exact 4-way split); fish structurally doesn't (40+ relevant groups, no small complete union) — coverage-based ranking was the pragmatic choice given the user's explicit "fish are a likely search class" priority. **Trade-off:** the fish list is deliberately incomplete (~33% of fish observations aren't covered by the 5 cached orders) — flagged as a real, open follow-up in the PLANNING doc, not a closed decision.
- **Decision:** kept the class-cache and order-cache as separate files (`gbif_common_class_keys.json`, `gbif_common_order_keys.json`) rather than one merged cache. **Why:** the validation/resolution logic in the plan is scoped by rank (check the cache for the requested rank first) — separate files map directly onto that lookup structure.
- **Decision:** added full prompt/usage logging and a time/cost summary table to `intent_query_spike.py`, matching `species_narrative_cost_experiment2.py`'s style, after explicit user request. **Why:** consistency with the established prototype logging pattern from earlier sessions (18-19 July) — the first implementation pass under-logged relative to that established norm.
- **Decision:** validated 3 of the plan's 10 test intents live (real API + real LLM calls) rather than all 10, before pausing. **Why:** user-directed — picked the plants case (simplest, tracer bullet), the mixed-taxa case (stress-tests quota/degradation), and the fish+reptiles case (stress-tests both holding solutions at once, 9 parallel filters). The remaining 7 (insects, rare, birds, surprise-me, colourful, nonsense-taxon, and the reptiles/fish cases run individually rather than combined) are still open.

### Next steps

1. Run the remaining test intents from `PLANNING_INTENT_QUERY_210726.md` §8 (insects, "something rare", birds, "surprise me", "something colourful" qualitative-guardrail case, a deliberately nonsense/misspelled taxon, reptiles alone, fish alone) to fully close out manual validation.
2. Once validated, run the Sonnet-vs-Haiku model comparison for this task specifically (per the plan §7 — deferred, not assumed), following the same pattern as the 19 July cost experiments.
3. Full end-to-end integration: wire this species-selection step into waypoint ordering + map/narrative generation (currently a separate, isolated prototype — `waypoint_spike.py`/`species_narrative_cost_experiment2.py` are not yet connected to `intent_query_spike.py`).
4. Fish coverage gap remains open — revisit if real usage shows the uncovered ~33% matters (see PLANNING §2.5/§10).
5. Naming decision (Nature Walker vs. Nature Quest) still open.
6. Minor unresolved process item: the silent Write/Edit approval behaviour noted above — check permission mode state if it recurs.
