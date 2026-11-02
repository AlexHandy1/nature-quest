# Work Summary — 02 November 2026

## What was built

- `prototypes/scripts/semantic_search_fastembed_cold_start.py` — isolated cold-start
  spike: imports `fastembed`, loads `sentence-transformers/all-MiniLM-L6-v2` via ONNX,
  embeds the real 100-species corpus (`prototypes/artifacts/semantic_search_corpus.json`),
  same `"<common> (<scientific>): <extract>"` recipe as the sentence-transformers
  prototype. Writes `prototypes/artifacts/semantic_search_fastembed_cold_start_results.json`.
- `prototypes/scripts/server_semantic_search_fastembed_cold_start.py` — same "Ready in
  Xms" startup-timing pattern as `server_semantic_search.py`, swapped to fastembed, to
  measure cold start in a real Flask server process rather than an isolated script.
- `prototypes/scripts/semantic_search_index_fastembed.py` — full fastembed/ONNX
  counterpart to `semantic_search_index.py`: same corpus, same embedding-text recipe,
  same DuckDB `species` table shape (reuses `build_embedding_text`/`build_store` from
  the original module), swaps `load_model()`/`embed_query()` to fastembed. Builds
  `prototypes/artifacts/semantic_search_fastembed.duckdb`.
- `prototypes/scripts/server_semantic_search_fastembed.py` — full fastembed/ONNX
  counterpart to `server_semantic_search.py`: same Flask wrapper, same
  `web/index_semantic_search.html` UI, runs on port 5058 (original stays on 5057) so
  both backends can be compared side by side in a browser.
- Installed `fastembed` (0.9.1, pulls in `onnxruntime` 1.23.2, no `torch`) into the main
  `venv/` (Python 3.13, x86_64 mac) — installed cleanly, no dependency-resolution issues
  (contrast with the `torch`/`sentence-transformers` install problems from
  `WORK_SUMMARY_011126.md`).
- None of this is committed yet — all new files are currently untracked
  (`git status --short` confirms).

## What was explored / learnt

- **fastembed does support the exact model already in use**:
  `TextEmbedding.list_supported_models()` lists
  `sentence-transformers/all-MiniLM-L6-v2` (384-dim) directly, so this is a true A/B of
  "same model, different runtime," not a model swap.
- **Measured cold start, model cache warm** (fresh process each run, 2 repeats for
  stability):
  - Isolated script: import ~790ms, model load ~250ms, first query ~4-6ms → **total
    ~1.0-1.1s**, vs. the sentence-transformers baseline's measured **~8.8-9s**
    (`semantic_search_experiment_report_311026.md`: `torch`/`sentence_transformers`
    import 7.8s + model load 1.1s + first query 152ms). **~8-9x cold-start reduction.**
  - Real Flask server process (`server_semantic_search_fastembed_cold_start.py`, timed
    from process launch to the "Ready" print): **~1.5-1.8s**, vs. the original's
    **~8.8s**. Still a clear win (~5-6x) but less dramatic than the isolated figure,
    because Flask's own import/startup is a bigger fraction of a much smaller total.
  - First-ever run also pays a one-time ~3s HuggingFace model-weight download to local
    cache, same as the sentence-transformers baseline's first-download cost — excluded
    from the above, which are warm-cache figures.
- **Batch embedding throughput is NOT meaningfully different**: fastembed measured
  ~42-49ms/species on the 100-species corpus, vs. the sentence-transformers baseline's
  36ms/species batched — same ballpark. Confirms the earlier hypothesis that swapping
  runtime (torch → ONNX) fixes cold start specifically, not steady-state throughput.
- **Warm single-query embed is faster with fastembed** (~2ms p50 over 20 reps vs.
  sentence-transformers' measured 15.2ms p50), but both are already far below the
  ~1s latency budget, so this isn't the part that matters for the production decision.
- **Quality spot-check**: querying the fastembed-built index for "pollinators" returned
  Red Mason Bee as the top result, matching the sentence-transformers version's
  behaviour — consistent with it being the same model/weights, different executor.
- **fastembed's embeddings are already L2-normalized** (`np.linalg.norm(v) == 1.0`
  confirmed directly) — no extra normalization step needed before storing/querying via
  DuckDB's `array_cosine_similarity`.
- **Root cause of the cold-start gap, discussed with the user**: PyTorch is a
  general-purpose tensor library built for training (autograd, CUDA dispatch, a large
  C++ backend) initialized in full at `import torch`, regardless of whether it's used
  for anything beyond a frozen CPU-only inference model. ONNX Runtime only executes an
  already-converted, static inference graph — no training machinery to initialize.
  Same model, same weights, same math; different amount of engine overhead to load it.
- Separately clarified for the user, correcting earlier imprecise framing in-session:
  - **Fastembed does not reduce stored vector size** — its "quantized" (`Q`-suffix)
    model variants and ONNX optimization levels quantize *model weights*, not the
    *output embedding vectors* (confirmed via two independent checks, including
    reading the `qdrant/fastembed` GitHub repo directly). A fastembed MiniLM run still
    emits 384-dim float32 vectors.
  - **"Static embedding" models are not automatically torch-free.** The
    `sentence-transformers/static-similarity-mrl-multilingual-v1` model card loads via
    `SentenceTransformer(...)` and its architecture is a `torch.nn.EmbeddingBag` — so a
    static model loaded the standard way still pays torch's import cost. "Static"
    (no-attention inference) and "torch-free loading" are separate, independent axes;
    conflating them was a mistake corrected mid-session. model2vec's own package
    (numpy-only) is the one that actually avoids torch, but only for its own
    `potion-*` models, not as a general loader for other static model cards.
  - **Offline batch-embedding wall-clock time is dominated by Wikipedia extract
    fetching (~6.4h sequential / ~40min at 10x concurrency for ~106k species), not
    embedding** (~64min). Swapping embedding runtime/model doesn't shrink the
    fetch-stage cost.
  - Storage-size scaling corrected: the experiment's "~163MB" figure is for the full
    ~106k-species batch, not for 100 species (100 species ≈ 150KB at 384-dim float32).
    Scaled to the full fact-table species count (917,827, per
    `WORK_SUMMARY_201026.md`), full-corpus vector storage would be **~1.4GB raw
    float32** — flagged by the user as a significant addition next to the current
    818MB fact table. Mitigations discussed: float16 (~700MB) or int8 (~350MB)
    quantization of the *stored* vectors (independent of embedding library/model
    choice), a smaller-dimension model (e.g. SSE's Matryoshka-truncatable 512→256/128
    dims, with a measured NanoBEIR quality curve at each size), or scoping the corpus
    to the existing ~106k top-N common-names set instead of the full ~918k.
  - Storage size and cold start are not linked the way it first seemed — cold start is
    driven by model loading (library import + weight load), not by the size of the
    stored vector *data*, which is read lazily per-query via DuckDB/httpfs, not
    preloaded at startup. Storage size's main production-relevant risk is only if an
    *unrestricted* full-corpus similarity scan ever needs to materialize all vectors in
    memory — which the existing restrict-to-`candidate_cells` design is specifically
    meant to avoid, and which has only been verified cheap up to 106k rows
    (`semantic_search_scale_probe.py`), not yet at ~918k.
- model2vec and the "stable static embedding" (SSE) family
  (https://huggingface.co/blog/RikkaBotan/stable-static-embedding-in-practice,
  itself citing Tom Aarsen's
  https://huggingface.co/blog/static-embeddings) are siblings in approach
  (static lookup+pool, no attention, distilled from a transformer teacher),
  not the same model or a head-to-head-benchmarked pair — evaluated as a candidate
  for a later storage-reduction pass, not pursued further this session once the
  user confirmed fastembed/ONNX as the chosen path for loading/running the model.
- Discussed but deliberately parked for later: the taxon-gate design for routing
  (whole-token match against a new curated taxon-noun list, since there is **no
  existing closed taxon vocabulary** in production — `TAXON_GUIDANCE`
  (`app/backend/services/prompts/taxon_resolution.py`) lets the LLM map arbitrary lay
  terms open-endedly, with only two hardcoded lay-term expansions: reptiles → 4
  entries, fish → 7 entries). Needs further discussion before design is finalised.

## Decisions and trade-offs

- **Decision: use fastembed/ONNX Runtime as the production approach for loading and
  running the query-time (and likely batch) embedding model, confirmed by the user
  after this session's cold-start comparison.** **Why:** same model/weights/quality as
  the sentence-transformers prototype, ~8-9x faster cold start measured in isolation
  and ~5-6x in a real server process, no `torch` dependency (smaller image, no
  macOS/x86_64 wheel-availability problems like the ones hit in
  `WORK_SUMMARY_011126.md`), installed cleanly with no dependency resolution issues.
  **Trade-off:** none identified this session — batch throughput and quality are
  equivalent to the existing baseline, not worse.
- **Decision: did not pursue model2vec/SSE further this session once fastembed was
  confirmed.** **Why:** fastembed already resolves the cold-start problem that
  motivated looking at static embeddings in the first place, without the added
  trade-offs of a different embedding space (needs its own quality re-validation) or,
  for model2vec specifically, additional setup/training time. **Trade-off:** the
  storage-size reduction a smaller-dimension static model could offer (e.g. SSE's
  Matryoshka truncation) is left on the table for now — revisit only if the ~1.4GB
  full-corpus storage figure turns out to be a real blocker once corpus scope is
  decided.

## Blockers

None. The routing-logic design (taxon-gate) was explicitly parked by the user for
further discussion, not blocked by anything technical.

## Next steps

1. **Design and build the Wikipedia batch extract + embedding process**: confirm the
   target species set (the existing ~106k top-N common-names set vs. the full
   ~918k-species fact-table set — directly affects the ~163MB vs. ~1.4GB storage
   trade-off discussed this session), then write and run the batch job (reusing the
   `monthly_gbif_update_job.tf` Cloud Run Job pattern, per the phasing discussed:
   generate offline as a job → one-time manual write to GCS via the existing
   write-only SA → checkpoint real httpfs-read latency before any serving code depends
   on it).
2. **Finalise design and prototype of the routing logic (semantic vs LLM)** — picks up
   the taxon-gate discussion parked this session; needs a curated taxon-noun
   vocabulary (none exists in production today) and a decided mechanism for routing
   simple taxon-shaped queries straight to the existing LLM path without ever reaching
   semantic search.
3. **Confirm and test the production evaluation approach** — a standing gold-set eval
   slotted into the existing `tests/evals/` (`@pytest.mark.eval`) tier, covering both
   final-result relevance and the routing decision itself; depends on (2) being
   settled first (can't assert "correct route" without a defined cascade). Needs
   independently-labelled relevance judgments (the experiment's own tags were
   self-assigned) and pinned regression fixtures for fixed polygon/query pairs.
4. **Implement in production end-to-end**: manual upload/addition of the offline-
   generated embeddings (from step 1) → add model/embedding loading and the routing
   logic (from step 2) into the app → run against the agreed evals (from step 3)
   before considering this done.

Separately, not yet actioned: stage and commit this session's new prototype files
(`prototypes/scripts/semantic_search_fastembed_cold_start.py`,
`server_semantic_search_fastembed_cold_start.py`,
`semantic_search_index_fastembed.py`, `server_semantic_search_fastembed.py`, and their
artifacts under `prototypes/artifacts/`) — currently untracked.
