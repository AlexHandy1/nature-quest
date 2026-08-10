# Feature Ideas Backlog

A running list of ideas to revisit during planning. Not prioritised — add freely.

---

## Agent-led CLI interface

Expose nature walk data as structured outputs that feed into an agent loop or anyone's custom UI. Rather than baking a specific UI into the product, make the data layer the interface — an agent can query GBIF, score paths, and return structured JSON that a CLI, chatbot, or third-party UI can consume directly.

## Show raw observations behind a species' cluster marker

On click/selection of a species in the final map, show all of that species' raw underlying observations (not just the single hotspot marker), so users get a sense of the real distribution behind the cluster to help guide their walk — e.g. "this species is common across a wide area" vs. "this species was only seen in one tight spot." Prototyped in `prototypes/scripts/e2e_walk_spike_clustering.py` (click-to-reveal raw occurrence points + winning grid cell).

## Automated AI audio accompaniment (text-to-audio narration)

Convert the AI-generated walk narrative into spoken audio automatically, so users can listen hands-free while actually walking rather than reading text on their phone. Feeds directly off the existing narrative generation step in the AI-personalised walk intent pipeline — the text is already produced per waypoint/species, so this would add a text-to-speech stage to turn that into an audio track (or per-waypoint clips) the user can play as they reach each point on the route.

## Immersive 3D map experience (WebGL)

Explore WebGL for a genuinely 3D, game-like map experience (vs. the current 2D Leaflet-based prototypes) — closer to the depth/perspective feel of Zelda/Minecraft-style exploration than a flat top-down map can offer. Extends naturally to letting a user "walk" a generated route remotely first, in an immersive 3D flyover/street-level preview (Google Earth-style), to build familiarity and excitement with the actual route and waypoints ahead of time.

## Shareable walk link

Let a user share a link to a generated walk (species, waypoints, narrative) so someone else can open it directly, rather than every visitor having to submit their own NL query first. Surfaced during the invalid-query-handling design session (280726) while weighing stateless vs. server-side-cached designs for the new `/gbif-species-query` validation endpoint — a stateless, client-round-tripped design was chosen for that endpoint because this codebase has no server-side session state yet, but a shareable-link feature would be a legitimate future reason to introduce it. Appeal: a link is a low-friction way for one user's good walk to reach other people, potentially driving wider shared usage/virality rather than the product staying single-player only.

## Path to a fully autonomous software factory (CI/CD)

Longer-term direction: move toward AI agents writing, reviewing, testing, and deploying without human intervention. The current production-foundation spec (`docs/specs/spec-infrastructure-production-foundation-300726.md`) deliberately keeps a human in the loop for now; these are the specific things to revisit once the basics are working and the MVP is built, not before:

- **Automatic rollback on post-deploy smoke test failure.** Today's pipeline order is build → deploy → smoke test, so a broken deploy is briefly live before anything notices, with no automated rollback wired up. Fine for now (human notices, rolls back manually); worth building once the basics + MVP are solid.
- **Removing the human PR-merge gate.** Current decision: a human still merges the PR into `main`, which then triggers the full CI/CD pipeline — this is the deliberate human checkpoint for now. Moving beyond this toward full autonomy would need real automated PR-review checks (e.g. an AI review pass) as a substitute gate before auto-merge could be trusted, not just green CI.
- Staging environment considered and explicitly rejected as part of this — the intent is for production itself to be the single, verified, confidence-worthy deployment target, not to add a parallel environment.

## Daily email activity digest (internal tool)

An internal tool (not user-facing) that sends the maintainer a daily email summarising website activity — sourced from PostHog (client-side product events, and once built, server-side AI Observability `$ai_generation` traces) and Cloud Run/Cloud Logging (structured request/outcome logs). Would give a single daily digest of things like query volume, resolved/unresolved/no_results/gbif_unavailable outcome breakdown, guardrail triggers (rate-limited, daily-cap-reached), and LLM cost/token usage — rather than having to check PostHog and GCP Cloud Logging separately.

Surfaced while scoping `spec-tool-llm-guardrails-gbif-query-040826.md`, which adds the first structured logging (`REQ-017`) and both PostHog observability channels (`REQ-018`/`REQ-019`) this digest would draw on — worth revisiting once that slice is live and there's real traffic/log data to summarise. Complementary to, not a replacement for, the basic uptime/5xx alerting (`REQ-025`-`REQ-027`) in the same spec, which is real-time/threshold-based rather than a daily rollup.

## Rearchitect GBIF data dependency (latency, reliability, accuracy)

Move off live, per-query calls to the GBIF REST API (`occurrence/search`, `species/match`) toward a local/cached data store built from GBIF's bulk data, to fix problems hit directly in production code (`slice3_enhanced_query_to_route_ordering_on_map`, 100826):

- **Reliability**: parallelizing GBIF calls (`ThreadPoolExecutor`, one call per taxon group) immediately triggered `429 Too Many Requests` on a real multi-group query (7 fish + 1 bird + 1 insect groups), exhausting `MAX_RETRIES` and surfacing as `gbif_unavailable`. Currently mitigated by capping concurrency at `MAX_CONCURRENT_GBIF_REQUESTS = 3`, but that's blunt — it caps latency gains and the live eval suite still throttles when its 4 tests run back-to-back. A local store removes the rate limit as a constraint entirely.
- **Latency**: every query does at least one live `occurrence/search` round-trip per resolved taxon group — often paginated, since live pagination (300 records/request) doesn't scale for common taxa in dense areas (a single "birds in Retiro Park" query returned 55,756 occurrences, ~186 pages) — plus a `species/match` round-trip per group for resolution. GBIF's [AWS Open Data snapshot](https://registry.opendata.aws/gbif/) (bulk-access dataset) or a local index queried directly would cut this to in-process lookups, and is the production-scale answer to the fetch-scaling issue found in `WORK_SUMMARY_250726.md`, instead of ad-hoc mitigations like the prototype's count-then-fallback-year guard.
- **Accuracy**: the current lay-term → GBIF-rank resolution approach relies on hand-curated LLM worked examples (fish's 7-order union, reptile's 4-class union, etc.) for common cases, falling back to live `species/match` otherwise. A full GBIF backbone taxonomy catalogue loaded locally, with a vector DB (embeddings over scientific/common names and taxonomic metadata) for semantic search, would let any lay/vague term resolve via nearest-neighbour search against the real, complete taxonomy instead of curated examples or LLM recall.
- **Agentic access**: once data is local, worth also exploring an MCP server (adopt/fork existing options — `cyanheads/gbif-biodiversity-mcp-server`, `pipeworx-io/mcp-gbif`, `agentmorris/gbif-mcp-server`, `tyson-swetnam/gbif-mcp` — or build one) so the AI pipeline and any future agent-led interface (see [Agent-led CLI interface](#agent-led-cli-interface)) can query it through a standard tool-use interface rather than bespoke client code. Reservation: an MCP hop could add latency in a pipeline that needs to feel responsive — measure against the direct-lookup approach rather than assuming either way.

Scope these together rather than as separate investigations — reliability, latency, and accuracy all point at the same underlying fix (stop depending on live REST calls per query).
