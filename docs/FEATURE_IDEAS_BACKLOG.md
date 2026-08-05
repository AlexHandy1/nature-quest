# Feature Ideas Backlog

A running list of ideas to revisit during planning. Not prioritised — add freely.

---

## Agent-led CLI interface

Expose nature walk data as structured outputs that feed into an agent loop or anyone's custom UI. Rather than baking a specific UI into the product, make the data layer the interface — an agent can query GBIF, score paths, and return structured JSON that a CLI, chatbot, or third-party UI can consume directly.

## Draw your own map

Let users sketch or define a custom area (e.g. draw a polygon on a map) and get recommended walks within that boundary. Rather than the system picking the park, the user defines the search zone and receives scored route suggestions for it.

## AI-personalised walk intent (core differentiator)

Users state a goal or interest for that day — "Today I want to learn about plants", "I'm curious about insects", "show me something rare" — and the system generates a fully personalised species selection, waypoint set, and narrative guide from that input. This is only possible with AI: the same park produces near-infinite distinct walks depending on the intent, drawing on the full breadth of GBIF's species catalogue rather than a fixed set of featured species.

**Why this matters:** It is the primary thing that makes Nature Walker impossible to replicate without AI. A static app can show you the ten most common birds; only an AI-backed system can hear "I want to learn about fungi" and construct a coherent, accurate, engaging walk from GBIF occurrence data in seconds.

**Architecture implication:** The pipeline must be intent-driven from the start. Species selection, waypoint ordering, information retrieval, and narrative generation all receive the user's stated intent as a first-class input. Efficiency matters — the system needs to generate fresh combinations quickly enough to feel live, which shapes how GBIF queries are structured, how species info is cached or retrieved, and how narrative prompts are templated.

## WebGL 3D map experience

Explore WebGL for a genuinely 3D, game-like map experience (vs. the current 2D Leaflet-based prototypes). Aimed at the fantasy video game journey direction — closer to the depth/perspective feel of Zelda/Minecraft-style exploration than a flat top-down map can offer.

## Show raw observations behind a species' cluster marker

On click/selection of a species in the final map, show all of that species' raw underlying observations (not just the single hotspot marker), so users get a sense of the real distribution behind the cluster to help guide their walk — e.g. "this species is common across a wide area" vs. "this species was only seen in one tight spot." Prototyped in `prototypes/scripts/e2e_walk_spike_clustering.py` (click-to-reveal raw occurrence points + winning grid cell).

## Explore GBIF's AWS-hosted cache/snapshot for large queries

Live `occurrence/search` pagination (300 records/request) doesn't scale for common taxa in dense areas — a single "birds in Retiro Park" query returned 55,756 matching occurrences, requiring ~186 sequential paginated requests. GBIF publishes a bulk-access snapshot on AWS (their public dataset / Open Data on AWS listing) that may allow querying large result sets far more efficiently than paginating the live REST API one page at a time. Worth investigating as the production-scale answer to the fetch-scaling issue found in `WORK_SUMMARY_250726.md`, instead of ad-hoc mitigations like the prototype's count-then-fallback-year guard.

## Automated AI audio accompaniment (text-to-audio narration)

Convert the AI-generated walk narrative into spoken audio automatically, so users can listen hands-free while actually walking rather than reading text on their phone. Feeds directly off the existing narrative generation step in the AI-personalised walk intent pipeline — the text is already produced per waypoint/species, so this would add a text-to-speech stage to turn that into an audio track (or per-waypoint clips) the user can play as they reach each point on the route.

## Remote immersive walkthrough (WebGL / Google Earth-style) prior to walking

Once a route is generated, let the user "walk" it remotely first in an immersive 3D environment (WebGL, or something like Google Earth's flyover/street-level view) before doing it in person — a preview experience to build familiarity and excitement with the actual route and waypoints ahead of time. Related to the existing [WebGL 3D map experience](#webgl-3d-map-experience) idea above, but focused specifically on previewing the *already-identified route* immersively rather than general 3D map exploration.

## Shareable walk link

Let a user share a link to a generated walk (species, waypoints, narrative) so someone else can open it directly, rather than every visitor having to submit their own NL query first. Surfaced during the invalid-query-handling design session (280726) while weighing stateless vs. server-side-cached designs for the new `/gbif-species-query` validation endpoint — a stateless, client-round-tripped design was chosen for that endpoint because this codebase has no server-side session state yet, but a shareable-link feature would be a legitimate future reason to introduce it. Appeal: a link is a low-friction way for one user's good walk to reach other people, potentially driving wider shared usage/virality rather than the product staying single-player only.

## Path to a fully autonomous software factory (CI/CD)

Longer-term direction: move toward AI agents writing, reviewing, testing, and deploying without human intervention. The current production-foundation spec (`docs/specs/spec-infrastructure-production-foundation-300726.md`) deliberately keeps a human in the loop for now; these are the specific things to revisit once the basics are working and the MVP is built, not before:

- **Automatic rollback on post-deploy smoke test failure.** Today's pipeline order is build → deploy → smoke test, so a broken deploy is briefly live before anything notices, with no automated rollback wired up. Fine for now (human notices, rolls back manually); worth building once the basics + MVP are solid.
- **Removing the human PR-merge gate.** Current decision: a human still merges the PR into `main`, which then triggers the full CI/CD pipeline — this is the deliberate human checkpoint for now. Moving beyond this toward full autonomy would need real automated PR-review checks (e.g. an AI review pass) as a substitute gate before auto-merge could be trusted, not just green CI.
- Staging environment considered and explicitly rejected as part of this — the intent is for production itself to be the single, verified, confidence-worthy deployment target, not to add a parallel environment.

## GBIF MCP server for agentic data access

Explore adopting (or forking/building) an MCP server for GBIF so the AI pipeline gets abstracted, agentic access to biodiversity data — species search, occurrence queries, taxonomy lookups — as MCP tools instead of hand-rolled API client code. Would let the AI-personalised walk intent pipeline (and any future agent-led interface, see [Agent-led CLI interface](#agent-led-cli-interface)) call GBIF through a standard tool-use interface rather than bespoke query/pagination logic, and could simplify or replace parts of the existing GBIF client work.

Existing implementations to review before building anything new:
- https://github.com/cyanheads/gbif-biodiversity-mcp-server
- https://github.com/pipeworx-io/mcp-gbif
- https://github.com/agentmorris/gbif-mcp-server
- https://github.com/tyson-swetnam/gbif-mcp

Open questions: how each handles the large-result-set pagination problem already noted in [Explore GBIF's AWS-hosted cache/snapshot for large queries](#explore-gbifs-aws-hosted-cachesnapshot-for-large-queries), how actively maintained/licensed each is, and whether integrating one is preferable to the current direct-API-client approach or worth forking for this project's specific needs.

**Reservation — latency:** going through an MCP layer (extra process/tool-call hop, agent reasoning over which tool to call) could be slower than the current direct API-client calls, especially in a pipeline that already needs to feel responsive. This needs to be measured against the current approach before committing, not assumed either way.

**Potential upside — query surface area:** an agentic layer may handle a broader range of query types more gracefully than the current hand-rolled client — e.g. rarity-based queries ("show me something rare"), recency-based queries (recent sightings only), or other compound filters that would otherwise need bespoke logic per query type. Worth weighing against the latency reservation above rather than treating as a clear win.

## Daily email activity digest (internal tool)

An internal tool (not user-facing) that sends the maintainer a daily email summarising website activity — sourced from PostHog (client-side product events, and once built, server-side AI Observability `$ai_generation` traces) and Cloud Run/Cloud Logging (structured request/outcome logs). Would give a single daily digest of things like query volume, resolved/unresolved/no_results/gbif_unavailable outcome breakdown, guardrail triggers (rate-limited, daily-cap-reached), and LLM cost/token usage — rather than having to check PostHog and GCP Cloud Logging separately.

Surfaced while scoping `spec-tool-llm-guardrails-gbif-query-040826.md`, which adds the first structured logging (`REQ-017`) and both PostHog observability channels (`REQ-018`/`REQ-019`) this digest would draw on — worth revisiting once that slice is live and there's real traffic/log data to summarise. Complementary to, not a replacement for, the basic uptime/5xx alerting (`REQ-025`-`REQ-027`) in the same spec, which is real-time/threshold-based rather than a daily rollup.
