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
