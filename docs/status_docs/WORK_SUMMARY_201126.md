# Work Summary — 20 November 2026

## What was built

- `prototypes/scripts/wikipedia_batch_rate_limit_poc.py` — throwaway PoC testing
  real-world throughput of the Wikipedia REST `page/summary` endpoint against a
  1,000-species stratified sample (by `total_occurrence` decile) drawn from the
  existing ~240k starter manifest (`prototypes/artifacts/wikipedia_coverage_match_manifest.parquet`).
  Live-flushed per-request progress output (`flush=True` on every print, run with
  `python -u`) so runs are watchable in real time rather than buffering silently
  until exit or process kill.
- `prototypes/scripts/wikipedia_batch_action_api_poc.py` — the validated
  alternative: batches up to 20 species per request via the MediaWiki Action API
  (`action=query&prop=extracts&exlimit=max&redirects=1`), resolving the
  normalized → redirect → page chain back to each originally-requested title.
  Stores the full extract text per species in its output JSON
  (`prototypes/artifacts/wikipedia_batch_action_api_poc_results.json`), not just
  length/status.
- Both scripts read `WIKIPEDIA_CONTACT_EMAIL` from the environment (via
  `.env`, `python-dotenv`) rather than hardcoding any email in source — required
  by this session's privacy tooling, which blocks writing a real email address
  into a new file; the user set it themselves in `.env`.

## What was explored / learnt

- **The Wikipedia abstract-dump route is dead.** `enwiki-latest-abstract.xml.gz`
  404s at the documented "latest" path and is absent from recent dated dump
  directories (checked `dumps.wikimedia.org/enwiki/20261101/`). Confirmed via
  web search: Wikimedia discontinued the abstracts dump job in February 2025
  (Phabricator T382069). `Wikipedia:Database_download` still lists
  `abstract.xml.gz` but under a heading marked "(deprecated)", with no size or
  status given.
- **No lighter static-dump alternative exists for bulk lead-paragraph text.**
  The only full-content alternative found, `enwiki-<date>-cirrussearch-content.json.gz`
  (dumps.wikimedia.org/other/cirrussearch/), is 43,430,968,247 bytes (~43GB)
  compressed — whole-article text for all of English Wikipedia, confirmed via a
  live `curl -IL`; far oversized for ~240k lead extracts. Wikimedia Enterprise
  (Snapshot/On-demand API) exists as a "free account" option per
  `Wikipedia:Database_download` but offers no stated pricing/size detail and
  would be a new vendor integration for a one-time job — not pursued.
- **Wikimedia's actual published rate-limit policy** (mediawiki.org/wiki/Wikimedia_APIs/Rate_limits,
  stated as "new in 2026"): anonymous requests with a compliant User-Agent get
  200 req/min; requests with no identifying info get only 10 req/min; 3 or
  fewer concurrent requests is the documented best practice; 429 responses may
  carry `Retry-After`. This applies to both the Action API and REST APIs.
- **First REST PoC run (concurrency=3, paced at the documented 200/min) failed
  heavily from request ~350/1000 onward** — a sustained run of consecutive
  429s, not recovering after each request's single retry. Root cause found:
  the User-Agent sent had no real contact info (a generic placeholder forced by
  the privacy-tool block on hardcoding an email), which likely bucketed
  requests into Wikimedia's 10 req/min "unidentified" tier instead of the
  200/min "compliant bot" tier. **Correction mid-session**: the assistant
  nearly reran the same test (to fix output buffering) without first
  confirming the actual fix (the contact-email env var) was in place — caught
  by the user before rerunning.
- **Second REST PoC run, with `WIKIPEDIA_CONTACT_EMAIL` genuinely set via
  `.env`: 1,000/1,000 OK, 0 failures, 0 429s**, sustained for the full 300.0s
  run at exactly the target 200 req/min (3.33 req/s). Latency: mean 379ms,
  p50 145ms, p95 1,163ms, max 5,113ms. Extrapolated full-batch (239,880
  species) time: **~20.0h** — confirms the documented ceiling is the real
  sustained rate, not an artificially conservative floor.
- **Concurrency/burst experiment (concurrency=6, rate effectively unpaced at
  100,000/min target)**: real server allows significant burst capacity above
  the documented average — 741 consecutive requests succeeded cleanly at a
  sustained ~55 req/s (~3,291/min, far above 200/min) before a hard cutoff at
  request ~742, after which every remaining request (254 of the last 258)
  429'd even after retry, with no recovery in the remaining ~17s of the run.
  Net: 746/1000 OK, 254 failed, 18.2s wall clock. Confirms a token-bucket-style
  burst allowance exists, but its refill/cooldown rate is unknown and wasn't
  tested further — judged too risky to rely on for a ~240k-species production
  run without knowing that parameter.
- **Major finding: the MediaWiki Action API (`action=query&prop=extracts`)
  accepts up to 20 titles per single request** (`exlimit=max`, server-confirmed
  via `limits.extracts: 20` in the live response), with `redirects=1` resolving
  redirects server-side in the same call (e.g. "Blue tit" → "Eurasian blue
  tit", confirmed live via `curl`). Since Wikimedia's rate-limit policy is
  enforced per *request*, not per *article*, this multiplies effective
  species-per-minute throughput by up to 20x within the same 200 req/min
  policy ceiling.
- **Batched-API PoC validated this at the same 1,000-species sample (same seed,
  directly comparable to the REST run): 50 batches of 20, 1,000/1,000 species
  resolved, 0 failures, 0 429s, 15.0s wall clock.** Extrapolated full-batch
  time: **~1.0h**, vs. ~20h for one-species-per-REST-call. Extract quality
  spot-checked as equivalent to the REST API's `extract` field (specific,
  on-topic lead paragraphs; length stats across the sample: min 49, mean 299,
  max 2,054 characters). The "1,000 species resolved via a redirect" figure
  from the initial spot-check was a false signal — it was counting
  underscore→space title normalization, not real page redirects; not a
  concern.

## Decisions and trade-offs

- **Decision: do not pursue the abstract-dump or cirrussearch-dump routes
  further.** **Why:** the abstract dump is confirmed discontinued
  (Feb 2025); the cirrussearch dump is ~43GB and built for a different scale
  of problem (whole-Wikipedia full text) than ~240k lead extracts.
  **Trade-off:** none — these were ruled out on hard evidence (404, discontinued
  job ticket, confirmed oversized file), not a judgment call.
- **Decision: do not chase the burst/cooldown pattern found in the
  concurrency=6 experiment, despite its apparent ~1.2h extrapolated rate.**
  **Why:** the refill/cooldown window is unknown; getting it wrong at ~240k
  scale risks a worse failure/retry storm than the now-validated, reliable
  batched-Action-API path, which already beats the burst approach's
  theoretical rate (~1.0h) with a fully clean, zero-failure run.
  **Trade-off:** leaves a theoretically faster approach unexplored, judged
  not worth the added risk/complexity given a reliable ~1.0h option already
  exists.
- **Decision: User-Agent contact info is read from an environment variable
  (`WIKIPEDIA_CONTACT_EMAIL`) set by the user, never hardcoded by the
  assistant.** **Why:** this session's privacy tooling explicitly blocks
  writing a real email address into new source files; the correct pattern
  (per the tool's own guidance) is for the user to set it themselves via
  `.env` or an inline `!`-prefixed command. **Trade-off:** none — this is
  also better practice generally (no PII committed to source), and the user
  confirmed this is why the first REST PoC run failed (see above).
- **Decision: the production fetch step will use the batched MediaWiki Action
  API (20 titles/request) rather than one-species-per-REST-call.** **Why:**
  ~20x fewer requests for the same documented rate-limit policy, validated
  clean at 1,000-species scale (0 failures, 0 429s, equivalent extract
  quality) — drops the full-batch fetch time from ~20h to ~1h.
  **Trade-off:** slightly more complex per-request response parsing (must
  resolve the normalized → redirects → pages chain back to each original
  title), already implemented and validated in
  `wikipedia_batch_action_api_poc.py`.

## Next steps

Per the original 4-phase plan (a → fetch extracts, b → embed, c → build final
table, d → upload), with phase (a) now confirmed to use the batched Action API
approach at ~1h instead of ~20h:

1. **Build the production fetch script** (`app/backend/jobs/wikipedia_extracts_fetch.py`
   or similar name) in `app/backend/jobs/` — not `infra/` (Terraform only) and
   not importing from `prototypes/` (must be standalone, since prototype code
   can be deleted/changed at any time) — implementing the validated batched
   Action API approach (20 titles/request, `redirects=1`, resolve
   normalized/redirect/pages chain), with its own
   `app/backend/tests/test_wikipedia_extracts_fetch.py` matching the rigor of
   `test_monthly_gbif_update.py` (pure-logic functions unit tested directly;
   network calls mocked, not hit in tests).
2. **Build the embed script** (`app/backend/jobs/wikipedia_embed_extracts.py`)
   — fastembed/ONNX MiniLM, written fresh in this module (not imported from
   prototypes), reads the fetch output, writes `specieskey` + `embedding
   FLOAT[384]`.
3. **Build the table-assembly script** (`app/backend/jobs/wikipedia_build_embeddings_table.py`)
   — joins extracts + embeddings + manifest into the final upload-ready
   parquet schema.
4. **Build the upload script** (`app/backend/jobs/wikipedia_upload_embeddings_table.py`)
   — manual-trigger upload of the final parquet to the production GCS bucket;
   no Terraform/Cloud Run Job wrapper yet (explicitly deferred to a later
   slice per the user).
5. **Gitignore all generated output** from this pipeline (downloaded dumps if
   any, intermediate extracts, embeddings, final table) under a dedicated
   output directory in `app/backend/jobs/` — not yet added, needs doing before
   the production fetch script starts writing real output.
6. Run the production fetch script on the full ~240k-species manifest once (1)
   is built and tested — expected ~1h, with some checkpointing/chunking still
   worth keeping (per the earlier chunked-batch design) even though the
   revised ~1h runtime lowers the stakes considerably versus the original
   ~20h estimate.
7. **Port manifest generation into production, at the end of this slice** (not
   blocking) — replicate `wikipedia_coverage_match_manifest.parquet` and its
   generation script (`prototypes/scripts/wikipedia_coverage_check.py`) into
   `app/backend/jobs/`, per the user's explicit call to defer this since the
   existing prototype-generated manifest isn't a blocker for the fetch work.

## Session 2 — 24 November 2026

### What was explored / learnt

- **Clarified the Action API's `prop=extracts` parameter contract**, since the
  batched approach above uses it and its options weren't fully understood:
  `exintro` is a scope switch (lead section only vs. whole article eligible);
  `exsentences` (1–10) and `exchars` (max 1,200) are length caps applied
  *within* whichever scope `exintro` selected — independent, composable knobs,
  not alternatives to each other. `exlimit` caps how many titles' extracts
  come back per request (confirmed 20 via `limits.extracts` in live
  responses). `redirects` resolves redirects server-side, reporting the
  mapping in a `redirects` array.
- **Confirmed live that dropping `exintro` (to get full-article text) breaks
  batching**, independent of any length cap: a batch of 3–4 titles requesting
  full-article text (with or without `exchars=1200`/`exsentences=10`) returned
  real content for only the first title large enough to fill the response
  budget; the rest came back with empty extracts. This is the TextExtracts
  extension's documented "exlimit lowered automatically for whole-article
  requests" behaviour — the expensive step is rendering the full article
  server-side, not the final truncated payload size, so a length cap alone
  doesn't restore batching. Only `exintro=1` (restricting scope to the cheap
  lead-section render) keeps batching intact.
- **The REST endpoint (`/api/rest_v1/page/summary/{title}`) cannot batch at
  all** — titles are part of the URL path (one resource per request), with no
  parameter equivalent to the Action API's pipe-separated `titles=`. The
  batching win is specific to the Action API.
- **The original 31 Oct precision@5 validation (hit@5 23/24, mean P@5 0.60)
  was built on REST `page/summary` text**, confirmed via
  `semantic_search_corpus.py`'s own docstring and code
  (`WIKIPEDIA_SUMMARY_URL = ".../rest_v1/page/summary"`, no parameters). The
  production plan's chosen fetch method (Action API, `exintro=1`) returns
  different — generally longer — text for the same species than what was
  actually retrieval-tested, since the two services apply different
  extraction logic even when both are nominally "the lead."
- **Quantified the REST vs. Action-API text-length/truncation difference** on
  real data: loaded the two existing PoC result files
  (`wikipedia_batch_rate_limit_poc_results.json`,
  `wikipedia_batch_action_api_poc_results.json` — same 1,000-species
  stratified sample, same seed) and compared the 399 species present with a
  successful extract in both. Used the real MiniLM tokenizer
  (`sentence-transformers/all-MiniLM-L6-v2`'s cached `tokenizer.json`, loaded
  directly via the `tokenizers` library with `no_truncation()` — bypassing
  fastembed's `token_count()`/`TextEmbedding`, both of which silently cap
  their reported count at 256 regardless of true length, confirmed via a
  500+-word test string that still reported exactly 256).

  | | mean chars | mean tokens | p95 tokens | max tokens | truncated @256 |
  |---|---|---|---|---|---|
  | REST `page/summary` | 230 | 130 | 128 | 268 | 1/399 (0.3%) |
  | Action API `exintro=1` | 322 | 140 | 220 | 446 | 12/399 (3.0%) |

  Action's intro text is 1.41x longer on average (char-length ratio), but the
  median ratio is 1.00x — for half the sampled species, the two sources
  return identical-length text (short stub articles where the lead is a
  single paragraph either way); the difference is concentrated in species
  with longer, multi-paragraph leads. Truncation at the model's 256-token
  limit rises from negligible (0.3%) to modest (3.0%).

### Decisions and trade-offs

- **Decision: accept Action API (`exintro=1`) text as equivalent to the
  REST-sourced text the 31 Oct precision@5 result was validated against,
  without re-running that evaluation.** **Why:** the user judged the
  confirmed length/truncation difference (1.41x mean chars, 3.0% vs. 0.3%
  truncation) small enough not to warrant re-validation right now.
  **Trade-off:** the production embeddings will be built on text that hasn't
  itself been precision-tested — only its REST-sourced predecessor has. If
  retrieval quality in production looks off, this swap is the first thing to
  revisit.
- **Decision: for species whose extract exceeds the model's 256-token limit
  (confirmed 3.0% of a 399-species sample), do nothing special — let the
  tokenizer's default truncation apply.** **Why:** lead paragraphs are
  written summary-first, so truncation mostly drops later supporting detail,
  not core thematic content; this matches what the already-validated 31 Oct
  corpus did by default (no special truncation handling there either).
  Chunking + mean-pooling multiple segments was considered and rejected for
  this slice — real complexity (chunk splitting, multiple embed calls, a
  pooling step) for a ~3% edge case, with a genuine risk of diluting the
  retrieval signal (later article sections — taxonomy tables, distribution
  lists, references — are more likely to add noise than help a thematic
  query like "pollinators"). **Trade-off:** the ~3% of species with longer
  leads lose their tail content from the embedding; accepted as low-stakes
  given the above.
- **Decision: store the full (untruncated) extract text in the production
  fetch output, regardless of the 256-token embedding limit.** **Why:** keeps
  the option open to revisit truncation handling (e.g. chunking, a different
  model, bounding the fetch with `exsentences`) later without re-fetching from
  Wikipedia. **Trade-off:** marginally more storage for the ~3% of species
  whose full extract exceeds what the current model uses; judged negligible
  at this scale.

### Next steps (updated)

Supersedes item 1 above with the now-confirmed fetch-text decision: the
production fetch script (`app/backend/jobs/wikipedia_extracts_fetch.py`) should
request `exintro=1&explaintext=1&exlimit=max&redirects=1` (no `exsentences`/
`exchars` cap) and store the full returned extract text per species
unmodified; truncation to 256 tokens happens only at embed time, via the
embedding model's own default behaviour, with no custom truncation logic in
the fetch or embed scripts for this slice.
