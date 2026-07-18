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
