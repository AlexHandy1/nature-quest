# Work Summary — 29 October 2026

Ideation/design session: explored embeddings, ML, and RL feature ideas for Nature
Quest, starting from a recommendations prompt and narrowing through several rounds
of critique into two concrete, separately-scoped tracks. No code changes — docs only.

## What was built

1. **`docs/status_docs/PLANNING_EMBEDDINGS_FEATURES_291026.md`** — full planning doc
   covering two tracks: Track A (semantic species search via an off-the-shelf
   pretrained text-embedding model over Wikipedia extracts, plus a cost-reducing
   query-routing cascade) and Track B (a custom-trained joint embedding space over
   species co-occurrence + taxonomy + Wikipedia text + satellite-derived landscape
   per cell, powering upfront "what might be here" previews and post-query
   recommendations). Includes a suggested build order across both tracks.
2. **`docs/FEATURE_IDEAS_BACKLOG.md`** — two short entries added ("Semantic species
   search (Wikipedia embeddings)" and "Embedding-powered recommendations (upfront +
   follow-on)"), each pointing to the planning doc above.

## What was explored / learnt

- **Started broad, converged through critique.** Initial idea generation (via the
  `/generating-novel-ideas` skill) produced a six-item portfolio spanning
  species-co-occurrence embeddings, Wikipedia semantic search, a query-embedding
  LLM-call-skip cache, cell/landscape similarity, and embedding-drift monitoring.
  The user's follow-up questions progressively sharpened and partly dismantled this:
  pushed on why word2vec over existing text embeddings; pushed on why a cold-start
  "featured species" idea was basically hardcodable without embeddings at all (fair
  — it was); pushed the "species like this" idea to articulate what action it
  actually enables, not just a related-species list.
- **Embedding method selection, worked through twice.** First pass proposed
  skip-gram-style training on species co-occurrence; reconsidered under the
  "no live training infra" assumption toward a cheaper PPMI + truncated-SVD
  matrix-factorization approach (one `numpy`/`scipy` batch script, no training loop)
  — the Levy & Goldberg (2014) result that SVD-on-PPMI approximates skip-gram was
  the justification. When the user said training infra was actually fine to invest
  in (with cost monitoring), reconsidered again toward an actual trained two-tower
  contrastive setup, and separately explained why a BERT-style transformer is the
  *wrong* upgrade path specifically for the co-occurrence signal (no sequence/
  position structure in a bag of species per cell; nowhere near enough data to
  pretrain a transformer from scratch) — while confirming a pretrained
  transformer-family *text* encoder is exactly right for the Wikipedia-extract
  modality, since that's real language data benefiting from someone else's
  large-corpus pretraining. Landed on graph-based methods (node2vec/GraphSAGE/
  LightGCN, the PinSAGE lineage) as the correct "more modern than word2vec" answer
  for the co-occurrence/graph modality.
- **Worked through a concrete joint multi-modal embedding design** on request:
  species nodes seeded by frozen Wikipedia sentence embeddings, cell nodes seeded by
  frozen landscape embeddings (AlphaEarth/OlmoEarth), trained via small projection
  heads into one shared space using two-tower contrastive learning (InfoNCE) over
  species–cell positive pairs (from the fact table) and species–species positive
  pairs (shared genus/family, from the dimension table), with negative sampling and
  tunable loss weights (`λ1`/`λ2`). Flagged AlphaEarth (via Google Earth Engine,
  precomputed, lighter lift) as preferred over OlmoEarth (open-weight but requires
  sourcing imagery and running inference yourself) for the landscape modality, with
  verifying Earth Engine access/licensing as the first concrete thing to check.
- **"Species like this" alone is weak** — the user correctly identified that a bare
  similar-species list is either redundant (species already in the current result
  set) or a dead end (no path to act on it). Resolved by splitting into two distinct
  jobs: in-area deepening (a relationship note on an already-shown species) vs.
  out-of-area discovery (nearest-neighbor species elsewhere, paired with that
  neighbor's own best hotspot cell, as a concrete one-tap new-walk suggestion).
- **Cold-start ideation went through a full round of rejection and redirection.**
  An initial "featured species clusters, rotated daily" cold-start idea was
  rejected by the user as something hardcodable without embeddings. A follow-up
  list organized by technical "axes" (temporal drift, aggregate usage, combinatorial
  generation, session state, cross-modal) was also rejected as not user-experience-
  focused. Converged on a single simple, concrete feature instead: a live "what
  might be here" preview that reacts to the drawn area before any query, split into
  a "Confirmed here" tier (direct fact-table lookup, no embedding needed, ship
  first) and a "Likely here, based on similar habitat nearby" tier (landscape-
  embedding nearest-neighbor inference, clearly labeled as inferred, restricted to
  never merge with confirmed data) — framed as the one thing a hardcoded/curated
  list structurally cannot do, since it must answer differently for any arbitrary
  polygon a user draws, most of which have sparse/no direct GBIF coverage.
- **Overwhelm-control design for both preview tiers**, prompted by a direct
  question: fixed display cap independent of drawn-area size (with excluded count
  shown as context, e.g. "5 of 214 recorded here"); diversity-aware ranking via
  greedy maximal-marginal-relevance in embedding space rather than raw occurrence
  count, to avoid both too-many and too-samey; a per-taxonomic-group cap as an
  independent guardrail; a smaller cap plus confidence floor for the inferred tier,
  deduplicated against confirmed and suppressed when confirmed is already rich;
  progressive disclosure reusing `ResultsPanel`'s existing accordion pattern. Noted
  that the existing `_select_species_across_groups` quota/round-robin merge logic
  (already built for multi-taxon-filter result merging) is a direct precedent for
  this capping problem, not a new concept.
- **Explicit dead ends / rejected ideas, recorded so they aren't re-proposed**:
  cross-user collaborative filtering (user base too small, reopens a privacy
  surface beyond the current consent-gated, non-identity-linked design); a
  query-embedding LLM-call-skip cache (valid cost lever in principle, user judged
  not worth building at current traffic scale); embedding-drift ecological
  monitoring (parked — not clearly valuable for this product, and would need many
  more months of snapshot history before drift is distinguishable from the
  boundary-tie churn already documented in `WORK_SUMMARY_251026.md`).

## Decisions and trade-offs

- **Decision:** Keep semantic search (Track A, off-the-shelf pretrained text
  embeddings) and custom recommendation embeddings (Track B, trained from scratch on
  this app's own data) as two separate planning tracks rather than one unified
  effort. **Why:** they have fundamentally different cost/infra profiles (inference-
  only vs. a real training investment) and solve different problems (query
  understanding vs. discovery/recommendation). **Trade-off:** some shared
  infrastructure (the frozen text encoder) is reused across both, so there's no
  full isolation — acceptable since the reuse is additive, not a coupling risk.
- **Decision:** For Track B, use a two-tower contrastive architecture over frozen
  modality-specific features, not a from-scratch transformer and not (yet) a full
  GNN. **Why:** matches the actual scale of the data (not enough for transformer
  pretraining to pay off) and is a known, standard pattern (two-tower retrieval
  models, PinSAGE-style graph recommenders) rather than a novel architecture to
  debug from scratch. **Trade-off:** leaves real propagation-based refinement (GNN
  message passing) as an explicit future upgrade rather than building it now.
- **Decision:** Build order puts the "Confirmed here" live-preview UX before any
  embedding work, and Track A's cheap offline validation before Track B's bigger
  investment. **Why:** validates the interaction pattern and the cheaper idea first,
  before committing to custom training. **Trade-off:** defers the most
  differentiated capability (landscape-embedding inference for sparse-coverage
  areas) until after two earlier steps prove out.

## Next steps

Prioritised order, from `PLANNING_EMBEDDINGS_FEATURES_291026.md`:

1. Track A offline validation — embed ~200 Wikipedia extracts, run ~10 thematic test
   queries, manually judge precision@5. No production code.
2. "Confirmed here" live preview UX (direct fact-table lookup, no embeddings),
   including the capping/diversity logic — ships value independently and validates
   the interaction pattern.
3. Track A production routing cascade (lexical match → semantic fallback → LLM
   fallback), once step 1 looks good.
4. Track B Phase 1 — PPMI + truncated-SVD species co-occurrence embeddings (cheap
   `numpy`/`scipy` batch job, no training loop) — validates whether the
   co-occurrence signal is worth the bigger Phase 2 investment.
5. Track B Phase 2 — joint multi-modal space (two-tower contrastive, frozen text +
   landscape features) and the recommendation surfaces built on it — only once step
   4 validates and Google Earth Engine access/licensing for AlphaEarth is confirmed
   feasible.

Also carried over, unrelated to this session's topic: `WORK_SUMMARY_251026.md`'s
open items (no cleanup/rollback runbook for the monthly GBIF pipeline; cache
versioning against `CHECKLISTBANK_COL_XR_DATASET_KEY` not yet built) remain
untouched.
