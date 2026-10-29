# Feature Ideas Backlog

A running list of ideas to revisit during planning. Not prioritised — add freely.

---

## GCP setup

Review changing Cloud Run's 0-instances default starting point to limit the cold-start issue — shift to reviewing changing Cloud Run to min instances 1 (or explore other methods) to limit the cold-start latency issue (~20 seconds).

## Explore a self-hosted/fine-tuned model

Explore removing the LLM API dependency altogether and utilising a self-hosted open-source model/fine-tuning.

## Agent-led CLI interface

Expose nature walk data as structured outputs that feed into an agent loop or anyone's custom UI. Rather than baking a specific UI into the product, make the data layer the interface — an agent can query GBIF, score paths, and return structured JSON that a CLI, chatbot, or third-party UI can consume directly.

## Immersive 3D map experience (WebGL)

Explore WebGL for a genuinely 3D, game-like map experience (vs. the current 2D Leaflet-based prototypes) — closer to the depth/perspective feel of Zelda/Minecraft-style exploration than a flat top-down map can offer. Extends naturally to letting a user "walk" a generated route remotely first, in an immersive 3D flyover/street-level preview (Google Earth-style), to build familiarity and excitement with the actual route and waypoints ahead of time.

## Shareable walk link

Let a user share a link to a generated walk (species, waypoints, narrative) so someone else can open it directly, rather than every visitor having to submit their own NL query first. Surfaced during the invalid-query-handling design session (280726) while weighing stateless vs. server-side-cached designs for the new `/gbif-species-query` validation endpoint — a stateless, client-round-tripped design was chosen for that endpoint because this codebase has no server-side session state yet, but a shareable-link feature would be a legitimate future reason to introduce it. Appeal: a link is a low-friction way for one user's good walk to reach other people, potentially driving wider shared usage/virality rather than the product staying single-player only.

## Let users ask and learn more, and refine their walk

The pipeline currently stops at a species list and a walk, with no way to go deeper. A natural next step is follow-up questions about a species, and refining the walk from there.

## Path to a fully autonomous software factory (CI/CD)

Longer-term direction: move toward AI agents writing, reviewing, testing, and deploying without human intervention. The current production-foundation spec (`docs/specs/spec-infrastructure-production-foundation-300726.md`) deliberately keeps a human in the loop for now; these are the specific things to revisit once the basics are working and the MVP is built, not before:

- **Automatic rollback on post-deploy smoke test failure.** Today's pipeline order is build → deploy → smoke test, so a broken deploy is briefly live before anything notices, with no automated rollback wired up. Fine for now (human notices, rolls back manually); worth building once the basics + MVP are solid.
- **Removing the human PR-merge gate.** Current decision: a human still merges the PR into `main`, which then triggers the full CI/CD pipeline — this is the deliberate human checkpoint for now. Moving beyond this toward full autonomy would need real automated PR-review checks (e.g. an AI review pass) as a substitute gate before auto-merge could be trusted, not just green CI.
- Staging environment considered and explicitly rejected as part of this — the intent is for production itself to be the single, verified, confidence-worthy deployment target, not to add a parallel environment.

## Daily email activity digest (internal tool)

An internal tool (not user-facing) that sends the maintainer a daily email summarising website activity — sourced from PostHog (client-side product events, and once built, server-side AI Observability `$ai_generation` traces) and Cloud Run/Cloud Logging (structured request/outcome logs). Would give a single daily digest of things like query volume, resolved/unresolved/no_results/gbif_unavailable outcome breakdown, guardrail triggers (rate-limited, daily-cap-reached), and LLM cost/token usage — rather than having to check PostHog and GCP Cloud Logging separately.

Surfaced while scoping `spec-tool-llm-guardrails-gbif-query-040826.md`, which adds the first structured logging (`REQ-017`) and both PostHog observability channels (`REQ-018`/`REQ-019`) this digest would draw on — worth revisiting once that slice is live and there's real traffic/log data to summarise. Complementary to, not a replacement for, the basic uptime/5xx alerting (`REQ-025`-`REQ-027`) in the same spec, which is real-time/threshold-based rather than a daily rollup.

## Evaluation extensions

Broaden what the eval and smoke-test suites cover beyond the current query → species → route checks.

- **Extend end-to-end smoke test interactions**: the e2e smoke test currently exercises the query-to-route path but stops short of the downstream user interactions on the delivered walk. Add coverage for:
  - **Map drawing**: assert the generated route, waypoints, and species markers actually render on the map (correct positions, ordering, clustering behaviour), not just that the underlying data is produced.
  - **Audio assessment**: now that AI audio accompaniment is built, assert audio is generated per waypoint/species, plays back, and matches the narrative text.
- **Narration assessment — low-observation cases**: add more evaluations to cover low-observation scenarios (sparse taxa, thin areas, fallback-year guards), where the narrative has less real data to draw on and is potentially more likely to hallucinate, over-claim, or produce generic filler.

## Semantic species search (Wikipedia embeddings)

Let a query match species by description, not just taxonomy — "show me pollinators" or "animals that might live in a pond" — using an off-the-shelf pretrained text-embedding model over each species' existing Wikipedia extract, as a cheap fallback/complement to the LLM taxon resolver rather than a replacement for it. See `PLANNING_EMBEDDINGS_FEATURES_291026.md` for the detailed design.

## Embedding-powered recommendations (upfront + follow-on)

Beyond search, use a custom-trained embedding space over species co-occurrence (+ taxonomy, + Wikipedia text, + satellite-derived landscape per cell) to power dynamic recommendations: a live "what might be here" preview as an area is drawn, before any query, and "similar species elsewhere" / "similar places" suggestions after a walk resolves. Distinct from the semantic-search idea above — this is a bigger investment (custom training, not just an off-the-shelf model) justified by serving multiple surfaces from one space. See `PLANNING_EMBEDDINGS_FEATURES_291026.md` for the detailed design.
