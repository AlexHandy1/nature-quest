# ADR-014: Audio Narration Feature — Data Flow & Response Architecture

## Status
Accepted

## Date
2026-08-21

## Context

The audio-narration feature (spoken/written per-waypoint field-guide narration) needed a production home after being validated as a series of throwaway prototypes (`prototypes/scripts/narration_*.py`, see `prototypes/README.md`). It's explicitly a secondary, optional action after a `/api/query` result — not part of the default flow. Building it raised several genuinely open questions: how does the Wikipedia extract needed for grounding get from GBIF/Wikipedia lookup to the narrative-generation step; how does the resulting audio get delivered to the browser; and how does this new, more expensive (LLM + TTS) feature share cost/abuse guardrails with the existing `/api/query` flow.

## Decision

**Stateless data flow.** The Wikipedia extract is fetched once during `species_enrichment.enrich_species()` (piggybacked on the image lookup that already happens there) and returned to the frontend as part of the normal `/api/query` response. When the user triggers narration, the frontend round-trips that same species data (name, coordinates, extract) back to the backend. The backend holds no narration-related session/cache state between the two requests.

**Synchronous single-call response.** `POST /api/narrate` performs narrative generation and TTS synthesis in one request and returns `{narrative, audio: <base64 mp3>}` — no separate audio-fetch endpoint, no object storage, no streaming. The caller gets a single audio blob already fully synthesized.

**Separate daily budget, same rate-limit category.** Narration has its own daily cost-guardrail counter (`services/narration_budget.py`), independent of `/api/query`'s (`services/query_budget.py`), so a burst of narration requests can't exhaust budget for the core query flow that every user relies on. Both endpoints reuse the same per-IP rate-limiting mechanism.

**Lightweight content-safety guardrail.** Because `/api/narrate` is directly reachable and accepts client-supplied species data, a guardrail is applied at the narrative-generation step to reduce the risk of the feature being used to produce inappropriate output, independent of a second moderation call or re-validating submitted data against GBIF/Wikipedia. This is a mitigation, not a deterministic guarantee — see the project's security review output for the residual-risk discussion. (Implementation detail deliberately omitted here — see `services/narration.py`.)

## Alternatives Considered

### Backend-cached/session-scoped species data (vs. stateless frontend round-trip)
- Pros: avoids sending extract text back and forth; single source of truth server-side.
- Cons: introduces the first piece of backend session state into an otherwise stateless request/response service; needs a cache key, TTL, and eviction policy for a feature explicitly scoped as optional/secondary.
- Rejected because: the frontend already holds the full species list in React state after `/api/query` resolves — passing the extract through as one more field costs nothing architecturally, and keeps the backend stateless.

### Two-step endpoint (generate narrative text, then a separate audio-fetch call) vs. one synchronous call
- Pros: lets the UI show a ready transcript immediately while audio finishes.
- Cons: the intended UI (button that goes straight from loading to a play control) means the frontend would just have to silently prefetch the audio before revealing play anyway — no real UX gain, and it doubles the endpoint surface and adds ID-correlation bookkeeping.
- Rejected because: it added complexity without addressing a real requirement.

### Base64-in-JSON vs. object storage (S3/GCS) vs. raw byte streaming for audio delivery
- Pros/cons discussed at length; object storage is the standard choice for audio that must persist, be shared, or be replayed across sessions/devices; streaming is the standard choice for low-latency "start playing before generation finishes."
- Rejected because: this app has no accounts, no persistence layer, and no requirement that narration survive a page reload or be shareable — it's a one-off, ephemeral addition to a single walk session. Object storage and streaming both solve problems this feature doesn't have. Base64-in-JSON is the simplest correct fit for a one-shot, non-replayed-across-sessions audio clip of this size.

### Shared daily budget with `/api/query` (vs. a separate one)
- Pros: one guardrail to reason about.
- Cons: narration has a materially different, higher cost profile per call (narrative generation + TTS vs. one small classification call) and is explicitly optional — bundling budgets risks a narration burst starving the core query flow.
- Rejected because: the cost-profile mismatch and secondary-feature status outweigh the simplicity of one shared counter.

## Consequences

- Narration's request payload is a bit heavier (species objects include the Wikipedia extract), and extract text transits the client — both judged trivial for this use case.
- The backend gains a second in-memory, per-process daily budget counter with the same known limitation as the existing one (doesn't share state across multiple Cloud Run instances) — see `ARCHITECTURE.md`'s infra section.
- `OPENROUTER_API_KEY` (the TTS provider credential) follows the same explicit Secret Manager fetch pattern established in ADR-006, for the same open-source-repo reasoning — not a new decision, just this decision's application to a second secret.
- If narration ever needs to persist, be shareable, or scale beyond a single ephemeral session, the object-storage/streaming alternatives above would need to be revisited — this ADR's reasoning holds only under the "ephemeral, single-session, no accounts" constraints that were true at the time of writing.
