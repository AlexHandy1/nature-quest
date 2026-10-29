# Planning: Embeddings-Powered Features — 29 October 2026

High-level reference plan from an ideation/design session exploring embeddings, ML,
and RL features for Nature Quest. Two genuinely distinct tracks fell out of the
discussion — kept separate here because they differ in cost, infra investment, and
what they're actually for. Not an exact spec — expect deviation as implementation
surfaces real constraints. Revisit and amend in place rather than superseding with a
new dated file, unless the plan changes substantially enough to warrant a fresh
snapshot.

## Track A — Semantic species search

### Problem

The current taxon resolver (`services/openrouter_taxon_client.py`) maps free text to
a structured `{taxonRank, taxonValue}` schema — it can only resolve queries that are
already taxonomy-shaped ("birds", "turtles"). It cannot answer a thematic/descriptive
query like "show me pollinators" or "animals that might live in a pond", because
there's no taxonomic string to map to. This is a real, current limitation, not a
hypothetical one.

### Design

Use an **existing, pretrained text-embedding model** (inference only, no training) —
not a custom-trained one. This is the right call specifically for text: natural
language pretraining at the scale that makes these models useful is not something to
reproduce in-house, and the Wikipedia-extract corpus here is a consumer of that
pretraining, not a training set for it.

1. **Offline, one-time batch job**: for every species with a Wikipedia extract
   (`services/wikipedia_client.py` already fetches these for enrichment), generate a
   sentence embedding via an existing model (e.g. a `sentence-transformers`
   MiniLM/BERT-family encoder). Store as a vector column keyed by `specieskey`, joinable
   to `species_dimension_with_common_names.parquet`. Re-run only when an extract
   changes or on the existing monthly refresh cadence.
2. **Query-time routing cascade**, cheapest check first — this is the actual design
   decision, not just "add an embedding lookup":
   - **Lexical/fuzzy match first** (DuckDB trigram/`LIKE` against the dimension
     table's own taxon names and common names) — free, no embedding, no LLM. Covers
     most of today's literal-taxon-name traffic.
   - **Semantic fallback** if (1) doesn't resolve confidently: embed the query text
     (one cheap embedding call, not a chat completion), compare against the
     precomputed species-extract embeddings, **restricted to species already present
     in `candidate_cells` for the drawn polygon** — this restriction is what keeps the
     result grounded even when the match is mediocre; it can never surface a species
     that isn't actually there.
   - **LLM fallback** only when neither of the above resolves with confidence, or the
     query is detected as compound/multi-group ("birds and butterflies" — a single
     embedding vector can't decompose this the way the LLM's structured tool output
     can).
3. Net effect: the LLM goes from "first and only resolver" to "expensive fallback for
   genuinely ambiguous or compound queries" — a cost reduction alongside the new
   capability, not just an addition.

### Open questions / risks

- Confidence threshold for the semantic-fallback step needs empirical calibration;
  false-confident thematic matches are the realistic failure mode.
- Compound-query detection (when to skip straight to the LLM) is currently a
  heuristic idea (conjunctions, multiple taxon-shaped nouns), not designed.

### Cheapest first test

Offline only: embed ~200 extracts, run ~10 thematic test queries, manually judge
precision@5. No production code, no API spend beyond the one-time embedding batch.

## Track B — Embedding-powered recommendations

### Problem

This is a bigger, separate investment: a **custom-trained joint embedding space**
over the app's own ecological data, justified by serving several recommendation
surfaces from one space rather than building each as a one-off. Deliberately not
reusing an off-the-shelf model here — the signal (which species actually co-occur in
the same place) doesn't exist in any pretrained model, it only exists in this app's
own 221M-row fact table.

### Why not an off-the-shelf language model for this part

Considered and rejected: training a BERT-style transformer from scratch on
species-co-occurrence data. Two reasons:
- **Wrong inductive bias.** A cell's species list is an unordered bag, not a
  sequence — there's no position/syntax for self-attention to exploit. The
  co-occurrence signal is fully captured by shared cell membership, which a
  co-occurrence/graph method matches directly.
- **Wrong data regime.** Transformers earn their complexity from massive
  pretraining corpora; ~106k species over a few hundred thousand cells is far too
  small to justify training one from scratch.

The right lineage for "more modern than word2vec" on this specific data is
**graph-based methods** (node2vec/GraphSAGE/LightGCN-style, the same family behind
production recommenders like Pinterest's PinSAGE), not language models. A pretrained
text encoder still has a role — see below — just not for the co-occurrence signal
itself.

### Proposed embedding method

**Phase 1 (cheap baseline, validate the idea first):** Build a sparse
cell × species incidence matrix from the fact table (restricted to the existing
top-N species set, ~106k, to bound scale), compute species × species co-occurrence
via sparse matrix multiplication, reweight with PPMI, and run truncated SVD. Pure
`numpy`/`scipy`, no training loop, no new infra — a single batch script, runnable
alongside the existing monthly GBIF job. This is the thing to build and sanity-check
first: train it, pick ~10 well-known species, and manually judge whether nearest
neighbors look ecologically sensible.

**Phase 2 (if Phase 1 validates, and training infra investment is accepted — cost
monitored like any other recurring job, not a live/per-request cost):** A joint
multi-modal space, not three separate embeddings for three separate features:

- **Species nodes**: initial feature = frozen Wikipedia sentence embedding (the
  pretrained encoder from Track A, reused here as a feature source, not retrained).
- **Cell nodes**: initial feature = a frozen landscape embedding per geohash cell,
  sourced from an existing geospatial foundation model (AlphaEarth via Google Earth
  Engine, preferred first — served precomputed, lighter lift; OlmoEarth considered as
  an open-weight alternative but requires sourcing imagery and running inference
  yourself, a heavier lift). **Earth Engine access/licensing/quota for this use case
  is the first thing to verify, before any other Phase 2 work.**
- **Trainable pieces**: small projection heads (a few linear layers) mapping each
  frozen feature into one shared space, trained with a two-tower contrastive
  objective (InfoNCE) over two positive-pair types pulled directly from existing
  data — species–cell pairs from the fact table (occurrence > 0), and species–species
  pairs from shared genus/family in the dimension table — with standard negative
  sampling (sample non-co-occurring cells / non-same-genus species). Loss term
  weights (`λ1` co-occurrence, `λ2` taxonomy) are the knobs to tune against training
  cost.
- **Scale handling**: subsample/downweight ubiquitous species-cell pairs (same idea
  as word2vec's frequent-word subsampling) so the training set stays tractable
  against 221M fact rows.
- **If this validates further**: upgrade the two-tower setup to actual graph
  propagation (GraphSAGE/LightGCN-style message passing over the same edges) — the
  PinSAGE-shaped next step, not a prerequisite for shipping value.

### Recommendation surfaces this space would power

**Upfront, before any query — "what might be here" live preview.** As a user
draws/selects an area (even the default fixed polygon), show two labeled tiers,
updating live as the shape changes:
- **"Confirmed here"** — direct fact-table lookup, no embedding needed. Ship this
  tier first, independent of any embedding work, to validate the live-preview UX
  pattern on its own.
- **"Likely here, based on similar habitat nearby"** — for areas with sparse/no
  direct occurrence coverage (most of the world a user might arbitrarily draw on),
  find nearest cells in landscape-embedding space and borrow their well-recorded
  species as labeled, non-hallucinated inference — never merged into "Confirmed".
  This is the one piece of this feature a hardcoded/curated list structurally cannot
  do, since it must answer a different, data-dependent question for every arbitrary
  polygon.
- Optional second inferred signal: given a few already-confirmed species, surface
  "often found alongside what's confirmed here" using the co-occurrence-embedding
  side specifically (a distinct kind of evidence from the landscape-based tier, keep
  visually separate).

**Overwhelm control for both tiers** (needed regardless of embeddings):
- Fixed cap independent of area size (reuse the `TOP_SPECIES_COUNT`-style pattern),
  with the excluded count shown as context ("5 of 214 recorded here") rather than
  scaling the shown count with area size.
- Rank "Confirmed" by diversity, not raw occurrence count: greedy maximal-marginal-
  relevance selection in the species embedding space (highest-occurrence first, then
  skip embedding-close candidates) — generalizes the existing
  `_select_species_across_groups` quota/round-robin merge logic already built for
  multi-taxon-filter results.
- Cap per taxonomic group as a guardrail independent of the embedding step.
- "Likely" tier: smaller cap than "Confirmed", a similarity-confidence floor, dedup
  against "Confirmed", and suppress entirely when "Confirmed" is already rich for
  that area.
- Progressive disclosure: collapsed by default, reusing `ResultsPanel`'s existing
  accordion interaction pattern, with an explicit "see all N" control.

**After a walk resolves — "species like this" / "similar places".** Two distinct,
separately-valuable jobs, not one feature:
- **In-area deepening**: a relationship note on an already-shown species ("often
  found with X here"), surfaced via the existing accordion detail — low-stakes
  enrichment of the current walk.
- **Out-of-area discovery hook** (the one with real product value): take the
  embedding nearest-neighbor of a species the user engaged with, look up *that
  neighbor's* own best hotspot cell (reusing the existing per-species hotspot
  selection already in `services/species_index.py`), and surface a concrete,
  one-tap new-area suggestion — converts a similarity score into a reason to take
  another walk, rather than a passive fact.
- **Direct user control**: once a space exists, offer an explicit "more like this" /
  "something different" pair of actions mapped onto embedding distance from the
  resolved walk's centroid — lets the user drive the strategy rather than the
  backend guessing at one.
- **Longer-term structuring, not a v1 requirement**: a small contextual bandit over a
  handful of named strategies (exploit-near, diversify-far, underexplored-region)
  using walk completion/engagement as reward, with arms defined as operations over
  the embedding space rather than arbitrary constants.

### Explicitly parked / rejected from this session

- **Cross-user collaborative filtering** — too small a user base for meaningful
  cross-user signal; also reopens a privacy surface beyond the current
  consent-gated, non-identity-linked PostHog design.
- **Query-embedding LLM-call-skip cache** (embed past query text, skip the LLM on a
  near-duplicate) — makes sense as a cost lever, but not worth building at current
  scale/traffic; revisit if query volume grows enough for cache hit rate to matter.
- **Embedding-drift ecological monitoring** (track monthly cell-embedding centroid
  drift as an early-warning signal) — parked, not clearly valuable for this product;
  would also need many more months of snapshot history before drift is
  distinguishable from the boundary-tie churn already observed
  (`WORK_SUMMARY_251026.md`).

## Suggested build order across both tracks

1. Track A offline validation (embed ~200 extracts, judge precision@5) — cheapest,
   fastest signal, no production code.
2. "Confirmed here" live preview UX, with capping/diversity logic — ships value with
   zero embeddings, validates the interaction pattern, and the capping logic is
   needed regardless of what comes next.
3. Track A production routing cascade (lexical → semantic → LLM), once (1) looks
   good.
4. Track B Phase 1 (PPMI+SVD species embeddings) — cheap custom-embedding baseline,
   validates whether the co-occurrence signal is worth the bigger Phase 2 investment.
5. Track B Phase 2 (joint multi-modal space) and the recommendation surfaces built on
   it — only once (4) validates and AlphaEarth access is confirmed feasible.
