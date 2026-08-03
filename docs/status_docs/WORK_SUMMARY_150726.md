# Work Summary — 15 July 2026

## What was built

- `prototypes/retiro_spike.py` — end-to-end data spike: fetches GBIF occurrences for Retiro Park (2026, polygon-bounded), selects top 5 species by count, fetches OSM footpaths, scores paths by species overlap within 200m, generates a Leaflet map at `prototypes/retiro_map.html`.
- `prototypes/overpass_spike.py` — isolated Overpass / OSM REST API test script used to debug path fetching independently of the main spike.
- `prototypes/requirements.txt` — prototype dependencies (`requests==2.32.5`). Kept inside `prototypes/` deliberately to avoid mixing with future production requirements.
- `prototypes/logs/.gitkeep` — logs folder tracked in git; log files themselves are gitignored.
- `.gitignore` — added `venv/`, `__pycache__/`, `*.pyc`, `prototypes/logs/*` (with `.gitkeep` exception).
- `~/.claude/skills/prototype/SKILL.md` — added rule: log runs with `tee` not in-code (`python <script> 2>&1 | tee prototypes/logs/<name>_$(date +%Y%m%d_%H%M%S).log`).

## What was explored / learnt

- **GBIF data for Retiro is solid:** 804 records, 126 distinct species, all with species names and coordinates. Fetched in 3 paginated calls (limit=300 each). The polygon geometry from the GBIF UI can be passed directly to the API via the `geometry` param.
- **Top-5-by-count produces all birds:** Pica pica (81), Picus sharpei (63), Anas platyrhynchos (53), Alopochen aegyptiaca (49), Cygnus atratus (43). No plants, insects or mammals in the top 10 at all — confirms the taxa diversity enforcement in the scoring formula is necessary, not optional.
- **200m threshold is too loose for Retiro:** Nearly every path scores 5/5, making the tie-break meaningless. The park is small enough that most paths are within 200m of all species observations.
- **Overpass API is unreliable:** `overpass-api.de` and `lz4.overpass-api.de` returned HTTP 406 (missing User-Agent) then HTTP 504 (server overload) across multiple attempts. `overpass.kumi.systems` returned 429 with an explicit "include a User-Agent" message. The root causes were: (1) no User-Agent header, (2) server instability on the public instances.
- **OSM REST API (`api.openstreetmap.org/api/0.6/map.json`) is the better approach:** Returns all elements in a bbox in one reliable call. 200 OK, 28,903 elements, 950 footways after filtering. Node coordinates come back as separate elements and must be looked up by ID to build way geometry — handled in `fetch_osm_paths()`.
- **Centroid was dead code:** Calculated per species but never used — removed.
- **In-code logging (Tee class) is unnecessary:** `python script.py 2>&1 | tee prototypes/logs/run_$(date +%Y%m%d_%H%M%S).log` is simpler and keeps the script clean.

## Decisions and trade-offs

**Decision:** Use `prototypes/` as a standalone folder separate from any future production code.
**Why:** No production code exists yet; keeping prototypes isolated avoids confusion about what is throwaway vs real.
**Trade-off:** Run command must reference `prototypes/` path explicitly.

**Decision:** Use OSM REST API (`/api/0.6/map.json`) instead of Overpass for path data.
**Why:** Overpass public instances are unreliable (504s, rate limits). OSM REST API returned 200 consistently and has no query language complexity for simple bbox fetches.
**Trade-off:** Returns all OSM elements (28k+) requiring client-side filtering; Overpass would return pre-filtered results. Acceptable for a park-sized area.

**Decision:** `requirements.txt` lives in `prototypes/`, not project root.
**Why:** Prevents confusion with future production dependencies (FastAPI, etc).
**Trade-off:** Install command references subdirectory: `pip install -r prototypes/requirements.txt`.

**Decision:** Log prototype runs with `tee` at the shell level, not in-code.
**Why:** Simpler, keeps prototype scripts clean. Recorded in `~/.claude/skills/prototype/SKILL.md` rule 6.
**Trade-off:** None — strictly simpler.

## Blockers

- **Path scoring tie-break is not meaningful:** 200m threshold causes nearly all 950 paths to score 5/5. The "winning" path is determined by Overpass/OSM API ordering, which is arbitrary. Need a secondary score (e.g. path length, total observation count within threshold, or tighter radius) before the route selection produces a useful result.
- **Taxa diversity not enforced:** Top 5 by raw count is all birds. The plan's scoring formula (`0.4 × seasonality + 0.4 × recency + 0.2 × spottability`) with taxa slot enforcement needs to be implemented before species selection is meaningful.

## Next steps

1. **Go deeper on paths logic** — figure out how to make the route a complete joined-up trip, not just a single highest-scoring line. The route should connect to all 5 selected species, touching each observation point: stitch together multiple OSM ways into a continuous walkable loop that passes through or near each species location.
2. **Review the map** — check that species markers and the "best path" render correctly and make spatial sense for Retiro.
2. **Fix path scoring tie-break** — add a secondary score (path length or total observation count within threshold) so the winning route is non-arbitrary.
3. **Tighten the distance threshold** — experiment with 50–100m instead of 200m to get meaningful path differentiation.
4. **Implement the scoring formula** — replace top-5-by-count with `0.4 × seasonality + 0.4 × recency + 0.2 × spottability` and taxa slot enforcement (1 bird, 1 plant, 1 wildcard, 2 open).
5. **Move to Sprint 1** once the spike findings are validated — FastAPI scaffold, GBIF query module, NatureAgent class, `POST /walk` endpoint.
