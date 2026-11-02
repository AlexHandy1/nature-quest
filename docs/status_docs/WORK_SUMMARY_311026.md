# Work Summary — 31 October 2026

Autonomous run of Track A's offline semantic-search experiment
(`PLANNING_EMBEDDINGS_FEATURES_291026.md`). Prototype-only: nothing under `app/`
touched. Full results: `semantic_search_experiment_report_311026.md`.

## What was built

- **`prototypes/scripts/semantic_search_corpus.py`**: 159 hand-curated global
  candidates. For each one it fetches the GBIF `species/match`, the global
  `occurrence/search` count, the Wikipedia REST `page/summary` and the GBIF English
  vernacular names, timing each request. It then selects 100 species spread across 18
  taxon groups × common/mid/rare tiers, written to
  `prototypes/artifacts/semantic_search_corpus.json`.
- **`prototypes/scripts/semantic_search_index.py`**: embeds
  `"<common> (<scientific>): <extract>"` with `sentence-transformers/all-MiniLM-L6-v2`
  into a DuckDB `FLOAT[384]` column (`prototypes/artifacts/semantic_search.duckdb`,
  780 KiB) and runs `array_cosine_similarity` top-k search.
- **`prototypes/scripts/server_semantic_search.py`** + **`prototypes/web/index_semantic_search.html`**:
  a Flask `POST /search` endpoint plus a search-only page (port 5057). The page reskins
  the trail-guide tokens, nav and accordion rows from `ux/species_detail_*_prototype.html`;
  there's no map and no LLM.
- **`semantic_search_experiment.py`** (latency + 24 scored scenarios + out-of-domain
  controls), **`semantic_search_ablation.py`** (embedded-text recipe comparison) and
  **`semantic_search_scale_probe.py`** (synthetic DuckDB search cost up to 106k rows).
  Result JSONs are in `prototypes/artifacts/`.
- **`prototypes/scripts/test_semantic_search.py`**: 46 unit tests, all passing.
- Docs:
  - `docs/status_docs/semantic_search_experiment_report_311026.md`
  - a `prototypes/README.md` §13 section
  - a results pointer under "Cheapest first test" in the planning doc
  - CPU-torch/sentence-transformers/duckdb added to `prototypes/requirements.txt`
  - repo-root `venv/` created (gitignored)

## What was explored / learnt

**Results:**
- hit@5 23/24, mean P@5 0.60.
- Warm query p50 ~24ms in-process and ~32ms over HTTP. Cold start ~9s, mostly the
  `torch` import.
- "something that flies" → birds fails: the top 5 are Spanish fly, fly agaric,
  housefly, dragonfly and red admiral. The ablation showed this happens with
  extract-only text too, so it's a polysemy weakness of the encoder, not the embedding
  recipe.
- Out-of-domain top-1 cosine ≤0.225 vs an in-domain median of 0.438, so a ~0.25–0.30
  floor is a plausible "send to LLM" trigger.

**Batch estimate for ~106k species:**
- Wikipedia: mean 218ms/request cold → ~6.4h sequential.
- Embedding: 36ms/species batched → ~64min.
- Storage: ~163MB of vectors.
- The second corpus run hit a warm CDN (36ms mean), so it isn't representative.
- The long-tail Wikipedia hit rate is unknown, because the curated set was 100% hits by
  construction.

**Search cost at scale:** DuckDB brute-force search is 115ms unrestricted at 106k rows,
but ~14ms when restricted to 2k candidate species.

**Data gotchas:**
- GBIF's backbone returns `class=None` for ray-finned fish.
- The first-listed GBIF vernacular name is often odd, so the most-cited one is now used.
- Some Wikipedia titles are scientific names. Both of these are now handled, with tests.

**Dead end — `agent-browser`:** Chrome lacked system libraries and there's no root or
apt lists. I resolved and extracted 50 Debian bookworm `.deb`s locally, which made
`ldd` resolve, but Chrome still hung even on a `data:` URL. I fell back to Node +
jsdom against the live server (DOM render, expand, HTML-injection inertness and
length validation all checked). Visual layout is still unchecked.

## Decisions and trade-offs

- **Decision:** global candidate set, tiered by global GBIF counts (user's choice).
  **Why:** variety. **Trade-off:** less tied to the app's Madrid data; `specieskey` is
  GBIF usageKey, not COL XR.
- **Decision:** a DuckDB array column instead of a dedicated vector DB (user's
  choice). **Why:** same engine as the production Parquet index. **Trade-off:** no ANN
  index, but the probe shows one isn't needed at 106k when restricted to candidate cells.
- **Decision:** hit@5 as the pass criterion (per the brief), with P@5 alongside it.
  **Why:** hit@5 alone is lenient (e.g. "swims" passes with a butterfly at #1).
  **Trade-off:** the relevance tags are hand-assigned and unreviewed.

## Blockers

- PR not opened: there's no `gh` CLI or token. Branch `explore_wikipedia_species_embeddings`
  is pushed; open it via
  https://github.com/AlexHandy1/nature-quest/compare/main...explore_wikipedia_species_embeddings?expand=1
  Do not merge without review, because merging to main deploys.

## Next steps

1. Sample ~500 random specieskeys from the production dimension table and measure the
   Wikipedia hit rate and cold latency. This needs the Parquet index locally.
2. Re-run the harness with one or two retrieval-tuned small encoders (change
   `MODEL_NAME`/`DIM`).
3. Grow the eval to ~50 independently labelled queries, then calibrate the confidence
   floor.
4. Cascade design: the lexical step must be whole-token only (substring matching would
   amplify the "fly" failure). Source conservation status from structured data.
5. Choose the serving approach (ONNX encoder vs hosted embeddings API) given the ~9s
   torch cold start.
6. Visually check `index_semantic_search.html` in a real browser.
