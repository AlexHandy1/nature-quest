# Work Summary — 30 August 2026

Docs-only session: no production code changed. Backlog grooming, a spec revision, and one live GBIF API check.

## What was built

- **`README.md`** — added one sentence to the intro paragraph surfacing the now-built AI-generated audio narrative guide (was not mentioned anywhere in the README).
- **`docs/FEATURE_IDEAS_BACKLOG.md`**:
  - Removed the "Automated AI audio accompaniment (text-to-audio narration)" idea — now built.
  - Added a `**Backbone taxonomy change**` sub-bullet under "Rearchitect GBIF data dependency" — implications of GBIF migrating its taxonomic backbone to Catalogue of Life (https://data-blog.gbif.org/post/catalogue-of-life-taxonomic-backbone/): taxon keys / name→rank resolutions can shift between backbone versions, curated LLM worked examples need revalidating, need a defined refresh process for a local store.
  - Added new theme **Evaluation extensions**: extend the e2e smoke test to cover map drawing and audio assessment; add narration evals for low-observation cases (sparse taxa, thin areas, fallback-year guards).
  - Added new theme **App clean-up and simplification refactor**: review `services/` for over-fragmentation / missing abstractions (repeated `resolve_api_key()`, consent-gated observability client, LLM call→parse→normalise→callback shape); fix mixed `*_client.py` naming conventions; general incidental-complexity sweep.
- **`docs/specs/spec-architecture-openrouter-taxon-resolution-280826.md`** — revised v1.0 → v1.1 per feedback:
  - Reframed around **model-agnostic abstraction as the deliverable**; Gemini 3.7 Flash is just the initial `MODEL` constant. New REQ-014 (no model-specific logic anywhere), AC-007 (grep confirms model string only at constant).
  - **Turtles case + evals now a hard ship gate**, not a `[NEEDS INPUT]`. New REQ-013: all cases in `test_taxon_resolution_eval.py` (25, Turtles → `class`/`Testudines`) **and** `test_full_pipeline_eval.py` must pass before production; close the Turtles gap by tightening `TAXON_GUIDANCE` wording (prompt edit, eval expectations unchanged). Updated AC-004, §2, §8, §9, §11, §12; added `test_full_pipeline_eval.py` to sources.
  - **Wall-clock timeout de-emphasised**: REQ-010 downgraded to "set `openai`'s built-in `timeout=` constant"; bespoke `ThreadPoolExecutor` mechanism explicitly out of scope. Reframed §2 (the 183s stall was on the *rejected* free model `gemini-2.5-flash-lite`, not the chosen one).
  - **Rollback-to-Anthropic stays test-covered**: new REQ-015 / AC-008 — retain ≥1 unit test exercising `anthropic_client.resolve_taxon_filters` with an Anthropic-shaped mock. (Interpretation of terse "test claude fallback" feedback; not a runtime auto-fallback, which stays out of scope.)
  - Added **"## Build steps at a glance"** section after the Introduction — 8 ordered steps with REQ refs.

## What was explored / learnt

- **Live GBIF check: `species/match` for Testudines at `rank=order` vs `rank=class`** (curl against `api.gbif.org/v1`):
  - Both return the identical match: `usageKey 11418114`, `rank: CLASS`, `matchType: EXACT`, `classKey: 11418114`. The `rank` param only nudges `confidence` (96 → 90). Backbone has exactly one node named Testudines, at CLASS rank, parent Chordata. **No order-rank Testudines node exists.**
  - App logic `services/taxon_resolution.py:14` does `match.get(f"{taxon_rank}Key")`. With `taxonRank="order"` it reads `match["orderKey"]` → **absent → returns `None`** → filter lands in `unresolved_groups`.
  - **Conclusion**: Gemini's "order" answer is fail-safe, not fail-wrong — the query degrades to `unresolved` ("couldn't resolve turtles"), it does not fetch wrong occurrences. Decided **not** to add this note to the spec (still fix via REQ-013's prompt tightening).

## Decisions and trade-offs

- **Decision:** OpenRouter spec's core deliverable is the model-agnostic abstraction, not the Gemini swap itself. **Why:** the point of PRD Slice 11 is cheap/safe model swapping; Gemini 3.7 Flash is the current cost-driven pick, expected to be revisited. **Trade-off:** slightly more up-front discipline (REQ-014) than a straight find-replace swap.
- **Decision:** No bespoke wall-clock timeout mechanism in the spec — plain `openai` `timeout=` only. **Why:** the 183s stall was a free-tier quirk of a rejected model (`gemini-2.5-flash-lite`); the chosen model showed no such behaviour across the full eval run. **Trade-off:** if a future model swap surfaces trickle-stall, that safeguard has to be built then.
- **Decision:** Ship gate is "all existing evals green", no accepted regression. **Why:** `test_taxon_resolution_eval.py` / `test_full_pipeline_eval.py` encode behaviour the product depends on; 24/25 is a prompt-iteration starting point, not a ship state. **Trade-off:** may need several `TAXON_GUIDANCE` iterations + eval re-runs before merge.

## Next steps

- User to push `model_optimisation` branch and merge PR themselves.
- Implement `spec-architecture-openrouter-taxon-resolution-280826.md` v1.1 (not started).
- Backlog themes added this session are unprioritised — revisit during next planning pass.
