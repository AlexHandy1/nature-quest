# Work Summary — 18 July 2026

## Strategic direction added

**AI-personalised walk intent** identified as the core product differentiator. Users state a goal ("Today I want to learn about plants") and the system generates a personalised species selection, waypoint set, and narrative from that input — something only possible with AI. The same park produces near-infinite distinct walks from the full breadth of GBIF's species catalogue.

This elevates next step 1 from the previous session (data + orchestration workflows): the narrative guide and species selection prototypes should be built intent-driven from the start, not with a fixed species list. Full entry added to `FEATURE_IDEAS_BACKLOG.md`.

**Architecture implication:** The pipeline must treat user intent as a first-class input across species selection, waypoint ordering, information retrieval, and narrative generation. Efficiency matters — the system needs to generate fresh combinations quickly, which shapes GBIF query structure, species info caching, and narrative prompt templating.

## Name change under consideration

**Nature Walker → Nature Quest.** "Quest" better signals the adventure and game-like feel intended for the UX, and aligns with the fantasy video game journey direction. Not yet confirmed — hold off on renaming files/repos until decided.

## What was built

- `planning_and_status_docs/FEATURE_IDEAS_BACKLOG.md` — added AI-personalised walk intent entry as core differentiator.
- `planning_and_status_docs/WORK_SUMMARY_180726.md` — this file.

## Next steps

1. **Prototype species info + narrative guide (intent-driven)** — build an agentic workflow that takes a user intent string, queries GBIF for matching species, retrieves species information, and generates a narrative guide per species. Keep it intent-driven from the start.
2. **Prototype UX options** — explore a fantasy video game-like journey feel for the walk experience.
3. **(Optional) Prototype species selection** — taxa diversity enforcement and weighted scoring (`0.4 × seasonality + 0.4 × recency + 0.2 × spottability`), all filtered through user intent.
4. **Compile learnings into an updated technical spec** — revised production spec with phasing/slices, evaluation framework for Retiro and rural Rascafría, full web deployment + CI/CD setup.
5. **Future / backlog:** Story arc agent ordering; OSM POI enrichment; agent-led CLI; draw-your-own-map.
