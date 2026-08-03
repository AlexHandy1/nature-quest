# Product Requirements Document: Nature Quest

**Version**: 1.1
**Date**: 2026-07-30
**Status**: Draft

This is the first PRD written for this project. It covers the full product vision — not just the MVP slice — synthesised from ten rounds of prototyping (`prototypes/README.md`), the session-by-session planning docs (`docs/status_docs/`), and open ideas in `FEATURE_IDEAS_BACKLOG.md`.

**Canonical prototype reference:** the most complete, current prototype implementation is `prototypes/scripts/e2e_walk_spike_full_validation.py`, `prototypes/scripts/server_full_validation.py`, and `prototypes/web/index_full_validation.html` — this is where the latest thinking and functionality (query validation gate, density-cluster hotspots, raw-occurrence points, polygon-draw support) all live together. Start here for what's already been built and validated. Earlier prototype scripts and session `WORK_SUMMARY_*.md` notes are useful for tracing back *why* a decision was made or resolving ambiguity, but should not be treated as the current state of the system.

---

## Executive Summary

Nature Quest turns public biodiversity data into a personalised, narrated nature walk. A user states what they're in the mood for — "show me some birds," "I'm curious about insects," "something rare" — and the system turns that into a real GBIF occurrence query, selects a small set of species genuinely recorded nearby, orders them into a walkable route through their sighting hotspots, and generates a narrated field guide grounded in that real data. The result is presented as an interactive, adventure-style "quest log" map.

The core differentiator, and the reason this needs AI rather than a static app, is that the same park can produce a near-infinite number of distinct walks depending on stated intent — an AI-backed system can hear "I want to learn about fungi" and construct a coherent, accurate walk from GBIF's full species catalogue in seconds; a static app can only ever show the ten most common birds.

Ten prototype rounds (`prototypes/README.md` §1-10) have proven the full pipeline works end-to-end — NL query → species selection → route → narrative → interactive map — on `claude-haiku-4-5-20251001`, for a fixed test park and for an arbitrary user-drawn area, for a few cents per run. Nothing in the codebase today is production code. This PRD's job is to turn that validated prototype surface into a coherent product shape and a slice breakdown that `/create-technical-spec` can work through, starting with the production foundation (deployment, testing, CI/CD, observability) rather than any further feature prototyping.

Nature Quest is being built **open source from its first commit** — code and documentation are public from the outset, not made public later once mature, so that other developers can contribute or fork and rebuild (see `docs/decisions/ADR-008-open-source-from-day-one.md`). This shapes several decisions throughout this PRD, most notably around security posture and CI/CD design (Slice 1/2, Technical Constraints, and Risk Assessment below).

---

## Problem Statement

**Current situation**: Apps like iNaturalist surface a large number of raw observations for a given area — but for well-observed locations this becomes an overwhelming array of dots on a map, with no easy way to explore, curate, or turn that data into an enjoyable walking experience, and no attempt to teach the user anything about what's actually there. Other walking/route apps handle navigation well but carry no biodiversity content at all. Neither responds to what a specific person wants to learn or notice on a given day.

The question this product exists to answer: **"How can I find interesting nature near me, and get out and experience it?"**

**Proposed solution**: An AI-personalised pipeline — intent parsing → real occurrence-data species selection → route ordering → grounded narrative generation → an engaging map presentation — that can generate a fresh, accurate, curated walk for almost any stated interest, in almost any location, on demand.

**Impact**: A walk that would otherwise be an unremarkable stroll becomes a small, personalised discovery experience — grounded in real, verifiable sightings and curated into something walkable and understandable, rather than a raw, overwhelming dump of observation points. [NEEDS INPUT: no quantified impact target yet beyond the MVP validation goal below.]

---

## Success Metrics

**MVP goal — demonstrate real-world value**: at least 20 distinct people generate 1+ walk each, with ≥90% of submitted queries producing a valid walk rendered on the map (i.e. not ending in an unhandled error or dead-end, whether that's a successful walk or a correctly-handled `needs_clarification` outcome). This is the first goal to validate — proof that people beyond the builder find the app worth using — before investing in deeper metrics.

[NEEDS INPUT: beyond this initial MVP goal, no deeper metric (repeat use, walk completion while physically out on it, narrative accuracy rate, etc.) has been proposed or agreed. Revisit once the MVP goal above is met.]

---

## User Personas

### Primary: The Casual Walker

- **Role**: Someone about to take, or already on, a walk — not bounded to an urban park or any specific setting. Could be a city park, a rural trail, a beach, a garden — anywhere with recorded biodiversity data.
- **Goals**: Learn something new on a walk that would otherwise be routine. Wants the walk itself to feel more interesting, not to become a serious naturalist or complete a checklist.
- **Pain points**: Existing nature-ID apps require you to already have something in front of you to identify; existing observation apps like iNaturalist show raw data with no curation into an experience; existing walking/route apps have no content layer. None respond to "I feel like learning about X today."

### Possible secondary: Parent making a walk more interesting for a young child

A parent looking to make a walk with a young child more engaging and educational, rather than just a walk from A to B. Needs further exploration — not yet validated as a distinct persona with different needs from the primary casual walker (e.g. simpler narrative language, child-appropriate content framing, shorter or safer routes). [NEEDS INPUT]

---

## User Stories & Acceptance Criteria

### Story 1: Personalised walk from a stated interest

**As a** casual walker **I want to** describe what I'm interested in today in plain language **so that** I get a walk built around that interest rather than a generic route or an overwhelming list of raw observations.

**Acceptance criteria:**
- [ ] A free-text query is turned into a structured GBIF taxonomy query (validated).
- [ ] Mixed-taxa requests (e.g. "birds, plants and mammals") return a genuine mix, not one dominant group (validated).
- [ ] Qualitative/descriptive requests with no GBIF equivalent (colour, "impressive") are dropped rather than guessed at (validated).

### Story 2: A walkable route through real sighting locations

**As a** casual walker **I want to** be guided between the actual places each species has been recorded **so that** the walk reflects real, curated data, not arbitrary points or an unfiltered dump of dots.

**Acceptance criteria:**
- [ ] Selected species' hotspots are ordered into a nearest-neighbour route from a start point (validated).
- [ ] Species hotspots use density-cluster centroids, not naive averages, when enough occurrences exist (validated).
- [ ] Waypoints that collide or sit implausibly close together (data artifacts, not real signal) are detected and surfaced, not silently rendered as stacked, illegible markers (validated). Actual merge-into-one-stop rendering is still open — see Technical Slices.

### Story 3: A grounded, narrated field guide

**As a** casual walker **I want to** get real information about each species I'll encounter **so that** the walk teaches me something accurate, not generic filler.

**Acceptance criteria:**
- [ ] Each species gets a GBIF common name, a Wikipedia-sourced description, and a generated narrative tying it to the walk (validated).
- [ ] Narrative generation is structured per-waypoint (not one flowing blob) to drive the map UI (validated).

### Story 4: Told clearly when the request can't be honoured as asked

**As a** casual walker **I want to** know when my request doesn't match anything nearby, rather than silently getting something different **so that** I can trust what I'm shown is what I asked for.

**Acceptance criteria:**
- [ ] No taxon signal resolves, or a given taxon fails resolution entirely → explicit `needs_clarification`, before any GBIF fetch (validated).
- [ ] A resolved taxon returns zero occurrences in this area → explicit `needs_clarification` (validated).
- [ ] Partial results (1-4 of 5 species) or close/overlapping waypoints proceed automatically with an explanatory note, since nothing was substituted (validated).
- [ ] User can explicitly choose to proceed with the unfiltered default instead of clarifying further ("Show most-observed instead") — never happens silently (validated).

### Story 5: Explore an area of my own choosing, not just a fixed default

**As a** casual walker **I want to** draw or otherwise define my own area **so that** I can get a walk somewhere the product doesn't have a built-in default for.

**Acceptance criteria:**
- [ ] A user-drawn polygon survives the full round trip: browser coordinates → GBIF WKT geometry → real occurrence search → route from the drawn area's own centroid → rendered map (validated).
- [ ] A fixed, cached default location (Retiro Park) is offered alongside draw-your-own, not instead of it. [NEEDS INPUT: "offer Retiro as quick default alongside draw-your-own" is a known open item — not yet built.]

---

## Functional Requirements

### Core Features

**Feature: Natural-language intent parsing**
- Description: One structured-output LLM call turns a free-text request into `taxonFilters` (rank + lay term, resolved to real GBIF backbone keys) and a sort preference.
- User flow: User types/speaks a request → parsed → resolved against local caches (kingdom/class/order) or a live `species/match` call → validated (`EXACT`, or `FUZZY` ≥85 confidence) → dropped if unresolved.
- Edge cases: Mixed-taxa requests split into parallel GBIF calls, merged via quota/round-robin with redistribution if a group underperforms. Unranked clade names (e.g. "Vertebrata") must never be treated as valid input — they resolve to wrong, unrelated organisms in GBIF's backbone.

**Feature: Species & route selection**
- Description: Resolved taxa are used to fetch real GBIF occurrences in the target area, ranked and selected to a target of 5 species, and ordered into a route via nearest-neighbour from a start point.
- User flow: Resolved query → parallel GBIF fetch per taxon group → rank/select → density-cluster hotspot per species → order into route.
- Edge cases: Scale guard/retry for common taxa returning very large result sets (prototype-only stopgap today, not production-grade — see Technical Slices); overlap detection for waypoints within 20m of each other.

**Feature: Query validation gate**
- Description: A cheap validation pass runs after species/route selection but before the expensive enrichment/narrative half of the pipeline, so the system never silently substitutes a different search than what was asked for.
- User flow: See Story 4 above (cases 1-4).
- Edge cases: Implemented and validated end-to-end in the current full-validation prototype, covering both the fixed-location and drawn-area flows.

**Feature: Enrichment & narrative generation**
- Description: Each selected species gets a common name, Wikipedia description, and a generated narrative connecting it to the specific walk.
- User flow: Species list → batched common-name/Wikipedia lookups → batched description call → structured per-waypoint narrative call.
- Edge cases: [NEEDS INPUT: no defined behaviour yet for a species with no usable Wikipedia entry, or narrative-generation failure mid-batch.]

**Feature: Interactive quest-log map presentation**
- Description: An adventure-style ("quest log") 2D map UI presenting the route, narrative, and species detail.
- User flow: Landing form (query + location) → loading state → interactive map with journal toggle, click-to-open species detail, mark-discovered, raw-occurrence-point toggle per species.
- Edge cases: Overlapping waypoints currently stack illegibly on the map (detected, not yet visually merged — open item).

### Out of Scope (for this PRD's initial technical-spec phase)

- WebGL 3D map experience (backlog idea, `FEATURE_IDEAS_BACKLOG.md`) — explicitly deferred behind the current 2D Leaflet interim choice.
- Agent-led CLI / third-party data interface (backlog idea) — a possible future distribution channel, not a near-term slice.
- Shareable walk links (backlog idea) — logged as a legitimate future reason to introduce server-side session state, explicitly deferred since it would answer a question ("expiry policy, memory growth, restart-loses-state") this project hasn't needed to answer yet.

---

## Technical Slices

This breakdown goes finer than the prototype-round boundaries and sequences work as: production foundation first, then the MVP feature set (everything already captured in the canonical full-validation prototype), then performance/cost/accuracy optimisations, then future directions. Two slices have no prototype coverage yet: security & abuse guardrails for the public LLM surface, and an LLM/AI provider abstraction layer (currently hardcoded to Claude).

| Slice | Status | Spec |
|---|---|---|
| **1. Production foundation** — hosting, testing framework, CI/CD (including safe handling of pull requests from forks), remote Terraform state, observability/monitoring, web analytics | Complete (2026-08-03) | `docs/specs/spec-infrastructure-production-foundation-300726.md` |
| **2. Security & abuse guardrails** — protecting LLM API keys, rate limiting/misuse protection on the public NL query textbox and any API routes. Given the codebase is open source (ADR-008), protections must not depend on an attacker being unable to read how they work | Not started, next priority | — |
| **3. NL query → intent parsing** — structured-output query parsing, taxon resolution, validation guardrails, basic misuse-guardrail exploration | Prototyped, verified (canonical: `e2e_walk_spike_full_validation.py`) | — |
| **4. Species selection, hotspot clustering & route ordering** | Prototyped, verified | — |
| **5. Query validation gate** — `needs_clarification` / auto-proceed-with-note UX | Prototyped, verified | — |
| **6. Overlapping-waypoint merge** — actually merging colliding markers into one shared stop (currently detect-and-annotate only) | Not built | — |
| **7. Enrichment & narrative generation** | Prototyped, verified | — |
| **8. Fixed-location flow** (Retiro Park as cached default) | Prototyped, verified; not yet the "offered alongside draw-your-own" default described in Story 5 | — |
| **9. Draw-your-own-area flow** | Prototyped, several open UX items remain | — |
| **10. Quest-log map UI** | Prototyped, verified (Variant A chosen over B/C) | — |
| **11. LLM/AI provider abstraction** — decouple the pipeline from being tightly bound to a single Claude model/component set; support provider/model swapping | Not started, new slice. Scoped after MVP, as a performance/flexibility optimisation | — |
| **12. Evaluation harness** — systematic, repeatable evaluation of species-selection and route quality (beyond prototyping's ad-hoc manual/live-test checks) | Not started, new slice. Scoped after MVP | — |
| **13. GBIF fetch & scale handling** — production-grade replacement for the prototype's count-then-fallback-year guard; possible GBIF AWS snapshot/bulk-access investigation | Prototype-only stopgap exists, not production-ready. Scoped after MVP | — |

---

## Technical Constraints

- **Performance**: GBIF's live `occurrence/search` pagination doesn't scale for common/unfiltered taxa in dense areas (one unfiltered Retiro "birds" query returned 55,756 occurrences, ~186 sequential page requests). This is a known risk, scoped as Slice 13 after the MVP rather than a launch blocker — the MVP's scoped default location and query patterns have already been validated at workable scale.
- **Cost**: Prototyping has run on `claude-haiku-4-5-20251001` at a few cents per full walk generation (3 LLM calls per walk). No production cost target/budget has been set. [NEEDS INPUT]
- **AI provider**: Currently hardcoded to Claude across all LLM calls (plain Messages API, not the Agent SDK). Slice 11 exists specifically to remove this constraint, once the MVP has proven the product concept.
- **Data source**: Entirely dependent on GBIF's public API and data completeness/quality — occurrence data has known artifacts (e.g. shared-recording-location duplicate coordinates) that the product must handle gracefully rather than assume away.
- **Security/compliance**: The NL query textbox (and any API routes built for it) will be exposed on the public internet, creating direct LLM API cost and misuse exposure. Protecting LLM API keys and building clear rate-limiting/misuse guardrails (e.g. against attempts to produce inappropriate content, or to run up API costs via abuse of the textbox or API routes) is required before public deployment — scoped as its own slice (Slice 2), completed before further feature or provider-abstraction work. Separately, plan to collect user analytics (queries submitted, click behaviour) via a tool such as PostHog — this likely requires cookie consent. [NEEDS INPUT: legal/consent approach not yet decided.] No other user data is stored at this stage (no accounts, no PII), so guardrails to protect *users'* data are intentionally lighter for now — the primary compliance concern at this stage is protecting the application host from cost/misuse exposure on the LLM API surface, not user data protection.
- **Open source**: the codebase and documentation are public from day one (`docs/decisions/ADR-008-open-source-from-day-one.md`), which changes the bar for the constraint above — no protective mechanism may rely on an attacker not being able to read its implementation, since the implementation itself will be publicly readable. It also means CI/CD (Slice 1) must be designed from the start to run safely against pull requests from forks, and that a LICENSE plus basic contribution scaffolding (`CONTRIBUTING.md`) are required setup items, not optional polish. [NEEDS INPUT: specific license choice not yet decided.]

---

## MVP Scope & Phasing

Per direction, this PRD is not MVP-only — it covers the full product vision, including slices pushing beyond what's been prototyped so far. Phasing below reflects sequencing, not an exclusion boundary.

### Phase 1: Production foundation
- Deployment architecture, testing, CI/CD, observability/monitoring, web analytics (Slice 1). Given the repo is open source from day one, this includes a remote Terraform state backend (never committed) and CI/CD designed from the start to run safely against pull requests from forks — not retrofitted once external contributions arrive.
- A LICENSE and basic contribution scaffolding (`CONTRIBUTING.md`), since the explicit intent is for others to fork/contribute. [NEEDS INPUT: license choice.]
- Security & abuse guardrails for the public LLM surface — protecting API keys, rate limiting/misuse protection on the NL query textbox and any API routes, and resolving the analytics/cookie-consent approach for PostHog (Slice 2). Must be in place before the app is exposed publicly, and ahead of any later provider-abstraction work. Because the codebase is public, guardrails must remain effective when fully readable, not rely on an attacker being unable to inspect them.

### Phase 2: MVP
Everything currently captured in the canonical full-validation prototype (`e2e_walk_spike_full_validation.py`, `server_full_validation.py`, `web/index_full_validation.html`), productionised: NL query → intent parsing, including at least some basic misuse-guardrail exploration (Slice 3); species selection, clustering & route ordering (Slice 4); query validation gate (Slice 5); overlapping-waypoint merge (Slice 6); enrichment & narrative generation (Slice 7); fixed-location flow as the cached default (Slice 8); draw-your-own-area flow (Slice 9); quest-log map UI (Slice 10).

### Phase 3: Known performance optimisations
- LLM/AI provider abstraction (Slice 11).
- Evaluation harness (Slice 12).
- GBIF fetch & scale handling (Slice 13).

### Future directions
Driven by ongoing user and developer feedback rather than fixed upfront. Candidates already identified: WebGL 3D map experience, shareable walk links, agent-led CLI/third-party interface, deeper GBIF-metadata-driven query features (e.g. a genuine "rare" or "recently observed" concept, explicitly deferred rather than built during the validation-gate round).

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Public NL query surface abused for cost overrun or inappropriate content generation | Medium-High (unmitigated once public) | High — direct financial/reputational exposure | Slice 2: security & abuse guardrails (rate limiting, key protection, misuse detection), completed before public MVP deployment |
| Cookie/analytics consent requirements not yet resolved for PostHog tracking | Medium | Medium — legal/compliance exposure if launched without proper consent | Needs legal/consent guidance before public analytics collection goes live |
| GBIF data artifacts (duplicate/shared coordinates) degrade map legibility | Confirmed occurs | Medium | Slice 6: overlapping-waypoint merge, within MVP scope; detection already ships as an interim mitigation |
| GBIF live-pagination scaling failure on common/unfiltered queries in dense areas | High (already observed in prototyping) | Medium — degrades UX for common requests, not a launch blocker given the MVP's scoped default location | Slice 13: GBIF fetch & scale handling, scoped after MVP as a known performance optimisation |
| Tight coupling to a single LLM provider limits cost/quality flexibility and creates a single point of failure | Medium | Low-Medium (acceptable for MVP) | Slice 11: LLM/AI provider abstraction, scoped after MVP |
| No systematic evaluation of species-selection/route/narrative quality — regressions could ship unnoticed | Medium | Medium | Slice 12: evaluation harness, scoped after MVP |
| No deeper success metrics beyond the initial MVP validation goal | Medium | Low-Medium | Revisit once the MVP goal (20+ users, ≥90% valid-walk rate) is met |
| Security controls that would only work if their implementation stayed hidden are ineffective, since the codebase is public from day one | Medium (a design-discipline risk, not yet realized) | High if it occurs | Slice 2 scoped explicitly around this constraint (ADR-007/ADR-008); no protection is considered complete if it depends on obscurity |
| CI/CD misconfiguration exposes deploy credentials to untrusted code via a pull request from a fork | Low if designed correctly from the start, High if retrofitted later | High — credential compromise | Slice 1's CI/CD design accounts for fork PRs from the outset (ADR-005/ADR-008), not as a later fix |
| No license yet in place — terms under which others may legally use, fork, or contribute are undefined | High (currently true) | Medium — blocks legitimate external contribution/forking, the explicit goal of going open source | License decision tracked as a Phase 1 setup item; flagged `[NEEDS INPUT]` |

---

## Dependencies & Blockers

**Dependencies**:
- GBIF's public `occurrence/search` and `species/match` APIs (`api.gbif.org/v1/`).
- Wikipedia for species descriptions.
- An LLM provider for intent parsing, description batching, and narrative generation (currently Claude only).
- A product analytics tool (e.g. PostHog) for the user-behaviour tracking described in Success Metrics/Technical Constraints.

**Known blockers**:
- No production hosting/deployment target chosen yet (Slice 1 not started).
- Security & abuse guardrails not yet designed (Slice 2 not started) — blocks public deployment.
- No license chosen yet — blocks legitimate external forking/contribution, the explicit reason the project is open source. [NEEDS INPUT]

---

## Appendix

### Glossary
- **GBIF**: Global Biodiversity Information Facility — the public API providing real species occurrence records this product is built on.
- **Hotspot**: A species' representative location for a given area, computed via density clustering of its real occurrence points (not a naive average).
- **Quest log**: The chosen adventure-style map/UI presentation (Variant A of three UX prototypes explored).
- **`needs_clarification`**: The validation-gate state returned when proceeding would silently substitute a different search than what the user asked for.

### References
- `README.md` — project overview.
- `prototypes/README.md` — authoritative map of what's been built, validated, and left open (§1-10).
- Canonical prototype implementation: `prototypes/scripts/e2e_walk_spike_full_validation.py`, `prototypes/scripts/server_full_validation.py`, `prototypes/web/index_full_validation.html`.
- `docs/status_docs/WORK_SUMMARY_290726.md` — most recent session, query validation gate, and the explicit next-steps this PRD builds on.
- `docs/status_docs/WORK_SUMMARY_250726.md` — density-clustering findings, GBIF data artifacts.
- `docs/status_docs/PLANNING_INTENT_QUERY_210726.md` — NL query → GBIF species selection design.
- `docs/FEATURE_IDEAS_BACKLOG.md` — unprioritised future ideas referenced throughout this PRD.
- `docs/decisions/` — architecture decision records, including ADR-008 (open-source-from-day-one posture) and its consequences for Slices 1 and 2.
