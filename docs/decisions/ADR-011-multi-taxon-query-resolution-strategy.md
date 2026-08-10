# ADR-011: Multi-Taxon Query Resolution Strategy

## Status
Accepted

## Date
2026-08-09

## Context

`/api/query` originally supported exactly one taxon filter per query (`taxonFilter: {taxonRank, taxonValue} | null`), resolved via a single live GBIF `species/match` call and a single `occurrence/search` call. This meant:

- Mixed-taxa requests (e.g. "birds and plants") could only ever return one group — the LLM was instructed to "pick whichever is mentioned first."
- Lay terms with no single clean GBIF rank — "fish" (ray-finned fish alone span ~46 orders, plus several unrelated classes for sharks/lampreys/etc.) and "reptiles" (GBIF retired the `Reptilia` class in favour of 4 separate classes) — resolved to nothing at all. "Show me some fish" returned an empty result even though Retiro Park genuinely has fish (carp confirmed present via live data).

This was a known, deliberate limitation of the prior slice (see `WORK_SUMMARY_050826.md`), not a bug — the single-filter schema simply couldn't represent these cases, so the team explicitly deferred curated multi-key caches at that time. This ADR covers lifting that limitation.

A prototype (`prototypes/scripts/e2e_walk_spike_full_validation.py`, designed in `PLANNING_INTENT_QUERY_210726.md`) had already solved this shape of problem: a `taxonFilters` list, one GBIF call per resolved filter, and a quota/round-robin merge across groups. This ADR is about porting that design to production and the specific decisions made while doing so.

## Decision

1. **Schema**: `/api/query`'s LLM-facing schema and response both move from a single filter to a list. `anthropic_client.resolve_taxon_filters` returns `list[dict]` (empty when there's no taxonomic signal, instead of `None`). The router resolves each filter's numeric GBIF key independently, drops any that fail to resolve, and surfaces which ones were dropped rather than silently absorbing them — response shape changes from singular `taxonRank`/`taxonValue` fields to `taxonFilters: [{taxonRank, taxonValue}]` (resolved groups only) plus a new `unresolvedGroups: [str]` field. Confirmed via `QueryForm.tsx` that the frontend only ever rendered `species`/`message`, never `taxonRank`/`taxonValue`, so this required no frontend changes.
2. **Fish/reptile lay-term expansion is LLM-taught, not deterministic.** The system prompt (`TAXON_GUIDANCE`) directly teaches the LLM two worked-example expansions — "reptiles" → 4 class-rank entries, "fish" → 7 entries (the curated coverage-ranked list, see below) — and the LLM emits the full multi-entry `taxonFilters` list itself for these lay terms. No code-side lookup table intercepts or rewrites the LLM's output for these cases.
3. **No curated kingdom/class/order cache reinstated for resolution.** `taxon_resolution.resolve_taxon_key` remains live-only (`species/match` on every filter, every query) — the prototype's curated JSON cache files (`prototypes/reference/gbif_*_keys.json`) were not promoted to production, consistent with the standing decision in `WORK_SUMMARY_050826.md`.
4. **Fish curated list covers 7 groups (~80.6% of occurrence volume)**, up from the prototype's original 5 (~67.9%): the 5 existing orders (Perciformes, Cypriniformes, Scorpaeniformes, Gadiformes, Clupeiformes) plus Salmoniformes (order) and Elasmobranchii (class, sharks/rays). Counts were re-pulled live this session (`occurrence/search?{order,class}Key=X&limit=0` across all 52 fish-related groups under Chordata) rather than reused from the 21 July session, since GBIF's occurrence counts grow over time and had shifted meaningfully (e.g. Perciformes: 32.1M → 35.9M).
5. **Species-group fetch/merge is a deep module in `gbif_client.py`**: `fetch_top_species(taxon_filters: list[dict], polygon: str = GBIF_POLYGON)` runs one `occurrence/search` per filter, ranks each group's species by count, then merges via `_select_species_across_groups` — `divmod(target_total, num_groups)` for the base quota, remainder to earlier groups, round-robin redistribution of any group's shortfall to groups with spare species. This is unconditional — a single-filter query is just the `num_groups=1` case of the same code path, not a special case.

## Alternatives Considered

### Deterministic code-side expansion for fish/reptiles
- Pros: zero hallucination risk — the LLM only needs to recognize "this is fish/reptiles," not recall 7 specific scientific names; a lookup table can't misspell or drop a group.
- Cons: another layer of special-casing to maintain; doesn't generalize to lay terms not yet curated.
- Rejected because: user explicitly chose to preserve flexibility and observe real LLM performance first, before committing to a harder-to-change deterministic table. Live eval runs this session (two independent live calls) showed Haiku reproducing the exact 7-group fish list correctly both times — no observed divergence yet, though this is not a large sample and remains the main open risk of this decision (see Consequences).

### Reinstating the curated kingdom/class/order cache for resolution
- Pros: near-zero latency and fully deterministic resolution for the common cases (kingdoms, common animal classes, fish/reptile groups) instead of a live `species/match` round-trip per filter.
- Cons: another config surface to keep in sync with the LLM's worked examples; the original blocker for removing it (single-filter schema couldn't represent multi-key concepts) is now gone, which made this tempting to reinstate.
- Rejected because: user chose to keep taxon resolution live-only for the same flexibility reasoning as above, at the cost of latency (up to 7 sequential live GBIF calls for a fish query, not yet parallelized — see Consequences).

### Fish coverage thresholds: 5 groups (67.9%) vs. 7 (80.6%) vs. 12 (90.3%)
- Considered all three based on live-ranked occurrence counts across all 52 fish-related GBIF groups.
- Rejected 5: too far from covering realistic fish sightings, and already flagged as a "holding solution" in the original design.
- Rejected 12: diminishing returns (5 more parallel GBIF calls per fish query for +9.7 points of coverage, vs. +12.7 points for the first 2 extra groups).
- Chose 7 (80%): the explicit "80/20" cutoff the user selected as the practical trade-off point.

### Full GBIF catalogue + vector DB semantic search (deferred)
- A more complete long-term alternative: embed the full GBIF backbone taxonomy and resolve lay terms via semantic nearest-neighbour search instead of hand-curated worked examples or lookup tables.
- Rejected for now, logged to `docs/FEATURE_IDEAS_BACKLOG.md` — worth revisiting if LLM-taught expansion accuracy/coverage proves insufficient with real usage.

## Consequences

- **Hallucination risk is accepted, not eliminated.** The fish/reptile expansions live entirely in the LLM's learned behaviour from the system prompt. A future query could return a slightly wrong, incomplete, or misspelled group list with no code-level guard to catch it. Mitigate by periodically re-running the live eval suite (`tests/evals/test_taxon_resolution_eval.py -m eval`) and watching for drift.
- **Latency is not yet addressed.** Both per-filter key resolution (`routers.query._resolve_taxon_keys`) and per-filter GBIF occurrence fetching (`gbif_client.fetch_top_species`) run their loops sequentially. A 7-filter fish query today means up to 7 sequential `species/match` calls plus 7 sequential (independently paginated) `occurrence/search` fetches. The prototype used `ThreadPoolExecutor` for this; production does not yet. Flagged as next-session follow-up, not solved by this ADR.
- **No GBIF call-volume guardrail exists yet** for the fact that one query can now trigger up to 7x the GBIF calls it used to (previously always exactly 1). Not yet a concern at current traffic; worth revisiting alongside the existing daily LLM-call budget (`query_budget.py`) if usage grows.
- **`fetch_top_species` gained a `polygon` parameter** (default `GBIF_POLYGON`, unchanged behaviour) purely to make the search area explicit and overridable for tests — this is scoped narrowly to that one function parameter; no user-facing or config-level polygon input exists yet. A future "draw your own map" slice (already on the backlog) will need to actually wire a real polygon input through the API, which this ADR does not attempt.
- **Response contract for `/api/query` changed**: `taxonRank`/`taxonValue` (singular) → `taxonFilters` (list) + `unresolvedGroups` (list). No frontend changes were needed this round since `QueryForm.tsx` never consumed those fields, but any future consumer of this API must be built against the new shape.
