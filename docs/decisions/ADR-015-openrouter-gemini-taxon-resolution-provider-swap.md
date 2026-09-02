# ADR-015: Taxon resolution moved to OpenRouter (Gemini 3.7 Flash), model-agnostic client

## Status
Accepted — implements `docs/specs/spec-architecture-openrouter-taxon-resolution-280826.md`. The spec's cited cost figures were revised during implementation; see Consequences below.

## Date
2026-09-02

## Context
`POST /api/query`'s NL → GBIF taxon filter step ran on the Anthropic SDK directly (`services/anthropic_client.py`, Claude Haiku). PRD Slice 11 calls for provider/model swapping as a planned post-MVP optimisation; a prototyping session (`WORK_SUMMARY_280826.md`) compared several OpenRouter-hosted models against the real 25-case taxon-resolution eval and found `google/gemini-3.7-flash` materially cheaper than Haiku with only one behavioral miss (Testudines rank) and no reliability issues, versus real correctness/reliability problems on the other candidates tried.

The spec scoped this narrowly: swap only the taxon-resolution call site to a **model-agnostic** OpenRouter client, leave narration (`services/narration.py`) on Anthropic untouched, and make a future model swap a one-constant change plus an eval re-run — not a re-architecture.

## Decision
- New `services/openrouter_taxon_client.py`: `MODEL = "google/gemini-3.7-flash"` is the only place a model name appears; `resolve_taxon_filters()` mirrors `anthropic_client.py`'s signature, imports `TAXON_GUIDANCE`/`QUERY_SCHEMA_TOOL` from it rather than duplicating them, and forces tool-calling via the OpenAI-compatible `chat.completions.create()` shape.
- `services/ai_observability.py` gained `build_openrouter_client()` — same consent-gating shape as the existing Anthropic branch, pointed at `https://openrouter.ai/api/v1`.
- `routers/query.py` swapped its taxon-resolution call site to the new client; `anthropic_client.py`'s `resolve_taxon_filters` stays intact, unused in production, and covered by its own test — a manual, tested rollback path (swap the import/constant back).
- `TAXON_GUIDANCE` (in `anthropic_client.py`, the single source of truth) gained one unconditional rule forcing Testudines to `class` rank regardless of phrasing, closing the one miss found in prototyping. Verified via a live re-run of both eval suites (25/25 taxon-resolution cases, full pipeline eval green).

## Alternatives Considered

### Stay on Anthropic Haiku (no change)
- Pros: zero migration risk, already in production and tested.
- Cons: strictly more expensive per token than the chosen alternative even after this ADR's cost correction (see Consequences) — no correctness or reliability reason to prefer it for this call site specifically.
- Rejected because: no material advantage found against the real eval suite that would justify the higher cost.

### Other OpenRouter-hosted models compared during prototyping (`gemini-2.5-flash-lite`, `deepseek-v4-flash`, etc.)
- Pros: some were cheaper still, or free-tier.
- Cons: real, observed reliability problems — `gemini-2.5-flash-lite` showed a 183.8s stall on one call despite a 60s configured timeout (per-read keep-alive trickle defeating `httpx`'s timeout semantics), and other candidates showed separate correctness or reliability issues not present in `gemini-3.7-flash`'s run.
- Rejected because: this slice's whole point is a safe, low-risk provider swap — trading reliability for a few more cents of savings wasn't a good trade at this stage.

### Runtime/env-var provider toggle instead of a build-time constant
- Pros: would allow switching providers without a redeploy.
- Cons: no concrete need for it yet at this project's current solo/pre-launch traffic profile; adds a live-flag surface for no proven benefit, contrary to this project's general default of avoiding feature flags absent a concrete need.
- Rejected because: the model-agnostic client design already makes a future swap cheap (one constant + eval re-run) without needing a live toggle — see CON-001 in the spec.

## Consequences
- **The spec's original cost rationale (§9: "62.5% cheaper," "~3.5x cheaper on one observed call") does not hold at the margin claimed.** Re-measuring against a live call post-implementation: OpenRouter's default "Balanced" routing landed on the "Google AI Studio" tier at **~$0.75/M input, ~$3.75/M output** (backed out from a real captured `usage.cost` value), not the **$0.375/M input, $1.875/M output** figure cited from the original prototyping session — roughly **2x** the originally cited price. `google/gemini-3.7-flash` is still cheaper than Haiku's $1.00/M input, $5.00/M output (roughly 25-33% cheaper per token at the measured rate), but by a materially smaller margin than the spec's headline numbers suggested. The likely cause is either a genuine OpenRouter price change between the prototyping session and this implementation, or the default "Balanced" routing mode not consistently landing on the cheapest available provider tier (OpenRouter lists three tiers for this model — Flex, standard, Priority — with a roughly 3.6x spread between cheapest and most expensive; see the model's OpenRouter listing for current figures, as these are not stable over time).
- **Provider routing is not currently pinned.** `openrouter_taxon_client.py`'s request does not set an explicit `provider` preference (e.g. `{"sort": "price"}`), so OpenRouter's default "Balanced" routing decides which of Google AI Studio / Google Vertex, and which pricing tier (Flex/standard/Priority), serves each call. This means the actual per-call cost is not fully predictable or pinned by this implementation — a follow-up worth considering if cost predictability becomes a concern, but not required for this slice's acceptance criteria.
- Model identity stays confined to the single `MODEL` constant (verified by `grep`) — a future model swap remains a one-constant change plus an eval re-run, independent of this cost correction.
- `services/anthropic_client.py`'s Haiku path remains fully intact and tested as a manual rollback if the OpenRouter path needs to be reverted.
