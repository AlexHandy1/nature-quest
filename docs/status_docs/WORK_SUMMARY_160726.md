# Work Summary — 16 July 2026

## What was built

- `planning_and_status_docs/FEATURE_IDEAS_BACKLOG.md` — new backlog file for feature ideas; seeded with two entries: agent-led CLI interface (structured data outputs for agent/custom UI consumption) and draw-your-own-map (user-defined polygon → recommended walks).
- `prototypes/osm_data_explorer.py` — OSM data exploration script. Accepts an optional bbox and label via CLI args (`python prototypes/osm_data_explorer.py "<bbox>" "<label>"`). Outputs: raw primitive examples (bare node, tagged node, way with node-to-coordinate resolution, relation), tag key frequency rankings for all ways and nodes, named features list, natural feature breakdown, leisure/amenity breakdown, and a JSON sample export (`prototypes/osm_sample_<label>.json`).
- `.gitignore` — added `prototypes/osm_sample*.json` to prevent sample exports being committed.
- `prototypes/waypoint_spike.py` — new prototype replacing the OSM path approach. Fetches GBIF occurrences, selects top 5 species by count, computes a mean-centroid hotspot per species, orders waypoints via nearest-neighbour from park centre, and generates a Leaflet map (`prototypes/retiro_waypoint_map.html`) with numbered pins, faint observation clouds, and a dashed connector line. No OSM data used. Run produced 5 bird species, 618m estimated walk, all hotspots clustered around the lake.

## What was explored / learnt

**OSM data model clarified:**
- Three primitives: Node (coordinate point), Way (ordered list of node IDs forming a line or polygon), Relation (named group of nodes/ways with roles).
- Ways do not carry coordinates — they reference bare node IDs; coordinates must be looked up from the node list. This is why constructing routes from OSM ways is fragile.
- Ways are drawn by individual human contributors; there is no guarantee footways form a connected, traversable graph.

**Retiro Park OSM data (bbox `-3.690,40.4076,-3.675,40.4216`):**
- 28,903 elements: 25,427 nodes, 3,177 ways, 299 relations.
- 534 individually mapped trees (85 named), 44 artworks, 44 historic memorials, named water bodies (El Estanque Grande, Estanque del Palacio de Cristal etc.), named gardens (La Rosaleda, Real Jardín Botánico), named park gates (10+ Puertas).
- 925 footways — the layer previously used for routing.

**Rural Lozoya valley OSM data (bbox `-3.887873,40.868548,-3.863668,40.884480`):**
- 5,756 elements: 5,587 nodes, 145 ways, 24 relations.
- Only 70 tagged nodes, 67 named elements, 6 natural features.
- What *is* mapped rurally is significant: named waterfalls (Cascada del Purgatorio with Wikipedia/Wikidata, elevation), named rivers and streams, named hiking trails (Ruta El Paular - El Purgatorio, GR 10), protected area boundaries (ZEPA Alto Lozoya, Parque Nacional de la Sierra de Guadarrama), ford crossings, named localities.
- Confirmed: Retiro's POI density is an edge case driven by global fame and decades of urban mapping effort. Rural coverage is sparse and selective.

## Decisions and trade-offs

**Decision:** Drop OSM data from the current prototype direction.
**Why:** The footway network is fragile (not a coherent graph, not designed for nature walks). The rich POI layer in Retiro is unrepresentative — rural areas have far less coverage, so building a product dependency on OSM POIs would fail to generalise.
**Trade-off:** Losing named POIs and natural features as potential story anchors for now. Can revisit OSM as an enrichment layer for a specific future feature (e.g. named water bodies, hiking trail overlays) once core walk generation is proven.

**Decision:** Move to a 5 GPS waypoints approach — GBIF species hotspots as pins, not OSM footpath scoring.
**Why:** Confirmed during grill-me session. Avoids fragile footway routing; puts species at the centre.
**Trade-off:** No curated route geometry; user navigates between waypoints freeform.

**Decision:** No Google Maps deeplink — just the dashed connector line on the Leaflet map.
**Why:** Google Maps deeplink opens a separate app/tab, creating a context switch. The Leaflet map is the discovery layer; navigation is the user's own concern.
**Trade-off:** User has no in-app turn-by-turn directions, but this matches the exploratory feel rather than a routed walk.

**Decision:** Nearest-neighbour ordering for waypoints (for now), with story arc ordering as a future agent-driven feature.
**Why:** Simple, deterministic, minimises total walk distance. Story arc (ordering by narrative logic — easiest to spot → rarest) identified as more interesting but deferred.
**Trade-off:** Geographic ordering may not match the most engaging species narrative sequence.

**Decision:** `osm_data_explorer.py` accepts bbox/label as CLI args rather than hardcoding.
**Why:** Enables comparison runs (Retiro vs rural) without duplicating the script.
**Trade-off:** None — strictly more useful.

## Next steps

1. **Prototype data + orchestration workflows** — explore agentic workflows for pulling in species information and developing the narrative guide (story around each species for the walk).
2. **Prototype UX options** — explore a fantasy video game-like journey feel for the walk experience.
3. **(Optional) Prototype species selection** — taxa diversity enforcement (1 bird, 1 plant, 1 wildcard, 2 open) and weighted scoring formula (`0.4 × seasonality + 0.4 × recency + 0.2 × spottability`).
4. **Compile learnings into an updated technical spec** — incorporate all prototype findings into a revised production spec and new phasing/slices, including:
   - Automated evaluation framework for Retiro and rural Rascafría location (expected species counts, quality metrics — think further on what good evaluation looks like)
   - Full web deployment and CI/CD setup
5. **Future / backlog:** Story arc agent ordering; OSM POI enrichment layer; agent-led CLI interface; draw-your-own-map feature.
