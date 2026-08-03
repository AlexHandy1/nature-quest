# Nature Quest

Turn public biodiversity data into navigable nature walks.

Given a location and a natural-language request (e.g. "show me some birds", "something rare"), Nature Quest dynamically selects the species currently present that match, designs a guided walk route through their recorded sighting hotspots, and generates a narrated field guide grounded in real observation data.

## Current approach (prototype stage)

Nothing here is production code yet — the project is at the throwaway-prototype stage, validating each piece of the pipeline in isolation before committing to a real architecture. The approach being validated:

1. **Species selection** — a natural-language walk request (e.g. "show me some birds") is turned into a structured GBIF query by an LLM, resolved to real GBIF taxonomy keys, and used to fetch real occurrence records for a location — either a fixed test location (Retiro Park, Madrid) or an arbitrary area the user draws on a map.
2. **Waypoint ordering** — the selected species' observation hotspots are ordered into a walkable route (nearest-neighbour from a start point — the park centre, or the drawn area's centroid).
3. **Enrichment + narrative** — each species gets a GBIF common name, a Wikipedia-sourced description, and a generated narrative guide for the walk.
4. **Presentation** — a game-like ("adventure-style quest log") map/journal UI, now triggerable from a local browser-based frontend backed by a minimal Flask server.

All of the above has been proven end-to-end (NL query → species → route → narrative → map) on `claude-haiku-4-5-20251001`, chosen after cost/time experiments showed no visible quality loss vs. Sonnet 5 for these call shapes — for both the fixed Retiro Park location and an arbitrary user-drawn area (see `prototypes/README.md` §8).

**See `prototypes/README.md` for what's been built, why, and how to run it** — that's the authoritative map of what exists in this codebase today.

## Planning docs

See `docs/` for the initial plan and session-by-session notes (`PLANNING_*.md` for design decisions, `WORK_SUMMARY_*.md` for what happened each session, in `DDMMYY` date order), architecture decision records (`docs/decisions/`), PRDs (`docs/prds/`), and technical specs (`docs/specs/`).

## Open source

Nature Quest is built in the open from its first commit — code and documentation are public from the outset, so other developers can contribute or fork and rebuild. See `docs/decisions/ADR-008-open-source-from-day-one.md` for what that implies for security posture and CI/CD design.

A LICENSE has not been chosen yet — until it is, the terms under which this code may be used, forked, or contributed to are undefined.
