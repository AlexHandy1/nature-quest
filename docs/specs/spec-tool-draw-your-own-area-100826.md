---
title: Draw-your-own-area flow (PRD Slice 9)
version: 1.0
date_created: 2026-08-10
last_updated: 2026-08-12
tags: [tool]
status: Built and deployed — implementation diverges from this spec's original
  area-selection state machine (REQ-005 through REQ-009); see
  docs/decisions/ADR-012-area-selection-persistent-widget-not-popup-funnel.md
  for what changed and why. Backend requirements (REQ-010 through REQ-017)
  were built as specified. Automated end-to-end coverage for this flow was
  not achieved — see the "known gaps" comment in tests/e2e_web_smoke_test.py.
sources:
  - app/backend/services/gbif_client.py (production)
  - app/backend/routers/query.py (production)
  - app/backend/models/query.py (production)
  - app/backend/services/waypoints.py (production)
  - app/backend/services/logging_client.py (production)
  - app/backend/tests/conftest.py (production)
  - app/frontend/src/components/MapView.tsx (production)
  - app/frontend/src/components/QueryPanel.tsx (production)
  - app/frontend/src/lib/posthog.ts (production)
  - prototypes/web/index_polygon.html (prototype, verified)
  - prototypes/scripts/server_polygon.py (prototype, verified)
  - prototypes/scripts/e2e_walk_spike_polygon.py (prototype, verified)
  - prototypes/reference/rascafria_area.geojson (prototype reference data)
  - docs/prds/nature-quest-prd-300726.md (Story 5, Technical Slices table)
  - docs/status_docs/WORK_SUMMARY_100826.md (next-steps, polygon_centroid() generalisation)
  - docs/status_docs/WORK_SUMMARY_250726.md (GeoJSON polygon prototype, scale findings)
  - docs/decisions/ADR-011-multi-taxon-query-resolution-strategy.md (public-endpoint validation posture)
  - This session's /grill-me transcript (2026-08-10)
---

# Introduction

This spec covers PRD Slice 9: letting a user choose between the existing fixed Retiro Park search area and a custom polygon they draw on the map themselves, with both paths feeding the same `/api/query` pipeline. It is meant to be implementable by an agent with no other context — read it fully before writing code. It assumes the reader has access to the current production codebase (`app/backend/`, `app/frontend/`) and the referenced prototype files but no memory of the planning conversation that produced it.

## 1. Purpose & Scope

**In scope:**
- A single map-first landing experience that offers a choice — explore the fixed Retiro Park area, or draw a custom polygon — via a popup overlaid on an already-rendered map (not a separate interstitial screen).
- Client-side geolocation, gated behind explicit user action, used only to center the map when entering draw mode.
- A Leaflet-based polygon-drawing tool, ported from the validated prototype (`prototypes/web/index_polygon.html`), integrated into the production `MapView` component.
- Client- and server-side polygon validation (minimum vertex count, maximum area).
- Making `polygon` a required field on `/api/query`'s request contract, removing the backend's hardcoded default.
- Docked post-result controls: new query (same area), redraw area, switch to Retiro.
- Structured logging of polygon coordinates per query, and a new consent-gated PostHog event for area-mode selection.
- Full test coverage per this project's TDD convention: backend unit, backend integration, live eval, frontend component, and an extended browser smoke test.

**Explicit non-goals for this slice:**
- Self-intersecting/invalid-geometry polygon detection or repair — deferred; only vertex-count and area-size validation are built now.
- Persisting a drawn polygon or chosen mode across a page refresh (no `localStorage`, no server-side session state) — a refresh always returns to the initial choice popup.
- Any redesign of the choice-popup UI beyond a functional two-option prompt (visual polish is a separate concern).
- Sending geolocation coordinates to the backend in any form, for any purpose (analytics, logging, or otherwise) — geolocation stays entirely client-side.
- Any change to how species are selected, clustered, or ordered — `fetch_top_species()` and `order_waypoints()` are reused unchanged; only the polygon and center point fed into them become dynamic instead of fixed.
- A dedicated `/api/waypoints` or other new route — this slice extends the existing `/api/query` contract only.

## 2. Verified Facts

- `services/gbif_client.py`'s `fetch_top_species(taxon_filters, polygon: str = GBIF_POLYGON)` already accepts an arbitrary WKT polygon string as a parameter — no change needed to its signature or internals for this slice. (production: `app/backend/services/gbif_client.py`)
- `services/gbif_client.py`'s `polygon_centroid(polygon_wkt: str) -> tuple[float, float]` already computes a centroid from any WKT `POLYGON((...))` string by averaging its vertices (excluding the closing repeated vertex) — already generalised to work for any polygon, not just the fixed Retiro one. (production: `app/backend/services/gbif_client.py`; confirmed in `docs/status_docs/WORK_SUMMARY_100826.md`, added specifically "sets up cleanly for future user-drawn-polygon support")
- `routers/query.py` currently computes `CENTER_LAT, CENTER_LON = polygon_centroid(GBIF_POLYGON)` **once at module import time** — this must become a per-request computation from the incoming polygon instead. (production: `app/backend/routers/query.py`)
- `models/query.py`'s `QueryRequest` currently has three fields (`query`, `distinctId`, `consent`) and no polygon field at all. (production: `app/backend/models/query.py`)
- The prototype (`server_polygon.py`) already validated the conversion from Leaflet's browser-drawn points (`[lat, lon]` pairs) to GBIF's WKT format (`"lon lat"` pairs, closed ring): `polygon_points_to_wkt()`. GeoJSON's coordinate order (`[lon, lat]`) already matches WKT's own order and needs no flip — only Leaflet's raw point format does. (prototype: `prototypes/scripts/server_polygon.py`; corroborated in `docs/status_docs/WORK_SUMMARY_250726.md`)
- The prototype's minimum polygon size is 3 vertices (`MIN_POLYGON_POINTS = 3`), enforced by disabling the confirm button client-side. (prototype: `prototypes/scripts/server_polygon.py`, `prototypes/web/index_polygon.html`)
- The prototype uses `leaflet-draw@1.0.4` (loaded via CDN `unpkg.com` in the prototype; not currently an npm dependency in `app/frontend/package.json`) for the draw toolbar, listening to `L.Draw.Event.CREATED`/`EDITED`/`DELETED`. (prototype: `prototypes/web/index_polygon.html`)
- The prototype's validated state machine is: intro → `#view-draw` (draw + confirm) → `#view-form` (query form, shows point count + "redraw" link) → `#view-loading` → `#view-map` (results, with "New Walk" = same area/new query, and "New Area" = back to draw). This slice's three-action docked bar (new query / redraw / switch to Retiro) is a direct evolution of that pattern, adding the third action. (prototype: `prototypes/web/index_polygon.html`)
- The GBIF live-pagination scale problem (55,756 occurrences / ~186 pages for an unfiltered "birds in Retiro" query) is unrelated to polygon *size* — it's driven by taxon breadth and area density together. The existing `SCALE_GUARD_THRESHOLD = 1000` probe-then-fallback-year mechanism in `_fetch_occurrences()` already applies regardless of which polygon is passed, with no change needed for this slice. (production: `app/backend/services/gbif_client.py`)
- `app/frontend/package.json` has no `leaflet-draw` dependency today — it will need to be added. `leaflet` `^1.9.4` and `react-leaflet` `^5.0.0` are already present. (production: `app/frontend/package.json`)
- `MapView.tsx` currently hardcodes `RETIRO_CENTER: [40.4137, -3.6826]` as a frontend-side duplicate of the backend's `polygon_centroid(GBIF_POLYGON)` result, with a comment noting it's "kept in sync manually since the frontend doesn't share the backend's `polygon_centroid()` computation." This constant becomes the default center for "Explore Retiro Park" mode; no change to its value. (production: `app/frontend/src/components/MapView.tsx`)
- `QueryPanel.tsx`'s existing "always resubmittable regardless of outcome" design (no one-shot/refresh-required behavior) is an explicit prior decision (`docs/status_docs/WORK_SUMMARY_100826.md`) — the new draw-mode controls must preserve this, not reintroduce a one-shot flow.
- `tests/conftest.py` provides three autouse fixtures (`reset_rate_limiter`, `reset_query_budget`, `reset_ai_observability_client`) that reset process-global state between tests — any new backend tests inherit these automatically, no changes needed there.
- `tests/e2e_web_smoke_test.py` is a deterministic Python script wrapping the `agent-browser` CLI, run manually (not in CI/CD), supporting both a local-dev mode and a `--url` live-deployment mode. It already exercises the existing fixed-Retiro flow end-to-end with exact DOM/response-body assertions. (production: `tests/e2e_web_smoke_test.py`)
- A real non-Retiro reference polygon already exists and was validated against live GBIF data in a prior session: `prototypes/reference/rascafria_area.geojson` (a mountain area near Rascafría, pasted from `latlong.net/polygon-drawer`). (prototype reference: `prototypes/reference/rascafria_area.geojson`; validated in `docs/status_docs/WORK_SUMMARY_250726.md`)
- `/api/query` is a public, unauthenticated endpoint (per `docs/decisions/ADR-011-multi-taxon-query-resolution-strategy.md` and Slice 2's stated posture in the PRD: "no protection may rely on an attacker not being able to read how they work"). Any client-side validation must be treated as a UX convenience only, never a security boundary.

## 3. Definitions

- **WKT**: Well-Known Text — the geometry string format GBIF's `occurrence/search` API accepts via its `geometry` parameter, e.g. `"POLYGON((-3.68876 40.4199,...))"`.
- **Fixed mode**: the existing default search area, Retiro Park, Madrid, backed by the constant `GBIF_POLYGON` in `services/gbif_client.py`.
- **Draw mode**: the new user-drawn custom polygon search area.
- **Area mode**: the frontend's local UI state distinguishing fixed vs. draw — not persisted, not sent to the backend as a field (only the resulting `polygon` value is sent).
- **Docked bar**: the persistent, always-resubmittable control panel shown once a query outcome is displayed (existing `QueryPanel` docked state).

## 4. Requirements, Constraints & Guidelines

### Geolocation

- **REQ-001**: Geolocation must only be requested via `navigator.geolocation.getCurrentPosition()` in direct response to explicit user action (a "Use my location" button click) — never automatically on page load, view mount, or mode selection.
- **REQ-002**: Before the "Use my location" button is shown, the draw-mode UI must display copy stating: it centers the map only, is never sent to the server or stored, and is a separate consent pathway from the site's analytics consent (`ConsentBanner`/`hasConsent()`).
- **REQ-003**: On successful geolocation, the map recenters (e.g. `map.setView([lat, lon], zoom)`) to the returned coordinates. The coordinates are used for this purpose only.
- **CON-001**: Geolocation coordinates must never appear in any request body sent to `/api/query` or any other backend route, and must never be written to any log (client or server).
- **REQ-004**: If geolocation is denied, errors, times out, or `navigator.geolocation` is unavailable, show a brief inline message (e.g. "Couldn't get your location — pan and zoom manually") and leave the map at its current center. The draw tool must remain fully usable regardless of geolocation outcome — this is never a blocking or hard-failure state.

### Entry flow & mode switching

- **REQ-005**: On load, `MapView` renders the map immediately, centered on `RETIRO_CENTER` at the existing default zoom — matching today's production behavior — with a choice popup/modal overlaid on top offering two options: "Explore Retiro Park" and "Draw your own area."
- **REQ-006**: Selecting "Explore Retiro Park" sets area mode to fixed mode using the existing `GBIF_POLYGON`-equivalent WKT string and `RETIRO_CENTER`, and dismisses the popup — the query form becomes usable immediately, matching today's existing behavior.
- **REQ-007**: Selecting "Draw your own area" dismisses the popup, shows the geolocation opt-in UI (REQ-002), and activates the Leaflet-draw polygon toolbar on the same `MapContainer` instance — no route change, no new mounted map component.
- **REQ-008**: Area mode is modeled as a single piece of state (e.g. `{mode: 'fixed' | 'draw', polygon: string, center: [number, number]}`) set through one function, called with different arguments for each of: initial fixed selection, a confirmed drawn polygon, a redraw, and a switch back to Retiro. There must not be separate, divergent code paths for fixed vs. draw beyond which UI controls are shown.
- **REQ-009**: Once a query outcome is being displayed, the docked bar exposes three distinct actions:
  - **New query** — resubmits with the current `polygon`/`center`, new query text (existing behavior, unchanged).
  - **Redraw area** — clears the current drawn polygon, returns to the draw toolbar on the map at its current center/zoom (no recentering, no geolocation re-request). Only available in draw mode.
  - **Switch to Retiro** — explicitly labeled, distinct button; calls the same area-mode setter (REQ-008) with fixed-mode arguments, recentering the map to `RETIRO_CENTER`.
- **CON-002**: "Redraw area" must never change the map's center or trigger geolocation — it only clears the existing polygon and re-shows the draw toolbar in place.

### Polygon validation

- **REQ-010**: A drawn polygon must have at least 3 vertices before it can be confirmed. The confirm control is disabled below this threshold, matching the prototype's validated pattern.
- **REQ-011**: A drawn polygon's bounding area must not exceed 25 km². Polygons larger than this are rejected before confirmation, with a clear inline message.
- **REQ-012**: `QueryRequest` (`models/query.py`) gains a new required `polygon` field (WKT string). There is no backend default/fallback — every `/api/query` request must include an explicit polygon, whether fixed-Retiro's WKT or a user-drawn one.
- **REQ-013**: The backend independently validates the incoming `polygon` field against the same two rules as REQ-010/REQ-011 (minimum vertex count, maximum area) before any GBIF or LLM work is performed. An invalid polygon returns a 4xx response immediately — client-side checks are advisory only, per CON-005.
- **CON-003**: Self-intersecting or otherwise topologically invalid polygons are explicitly out of scope for this slice (see Purpose & Scope). GBIF's real behavior on such input is not yet empirically confirmed — flagged as a follow-up investigation, not built as a guard now.
- **REQ-014**: `routers/query.py` derives its per-request center point via `polygon_centroid(body.polygon)` instead of the current module-level `CENTER_LAT, CENTER_LON` constants, and passes both the request's `polygon` and the derived center into `fetch_top_species()` and `order_waypoints()` respectively.
- **CON-004**: `polygon_centroid()` and `fetch_top_species()`'s `polygon` parameter are reused as-is (see Verified Facts) — no signature changes to either function are required by this slice.
- **CON-005**: Because `/api/query` is a public endpoint, server-side polygon validation (REQ-013) is the authoritative check. Client-side validation (REQ-010, REQ-011) exists purely to give the user fast feedback before submission and must not be treated as sufficient on its own.

### Logging & analytics

- **REQ-015**: The polygon submitted with each query is captured in the existing structured Cloud Run logging (`services/logging_client.py`), on the `log_query_submitted` line or an equivalent pipeline-stage log — visible in production logs for every request, consistent with existing per-request logging.
- **REQ-016**: A new consent-gated PostHog event fires on area-mode actions (initial mode selection, redraw, switch-to-Retiro), via the existing `trackEvent()`/`hasConsent()` pipeline in `src/lib/posthog.ts`, carrying which mode/action occurred. This event is subject to the same consent gate as all other PostHog tracking in the app — no special-casing.

### State & persistence

- **REQ-017**: No area-mode state (chosen mode, drawn polygon, center) is persisted to `localStorage` or any other client storage. A page refresh always returns to the initial choice popup (REQ-005), consistent with the app's existing no-server-session-state design.

## 5. Interfaces & Data Contracts

### `POST /api/query` request body (updated)

```json
{
  "query": "show me some birds",
  "distinctId": "anon-123",
  "consent": false,
  "polygon": "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
}
```

- `polygon` (string, required, new field): a WKT `POLYGON((...))` string, either the fixed Retiro constant or a user-drawn one, converted client-side from Leaflet points before submission.
- All other fields and the four response outcome shapes (`resolved`, `unresolved`, `no_results`, `gbif_unavailable`, plus the existing `rate_limited`/`daily_limit_reached` guardrail responses) are unchanged.

### `models/query.py` — `QueryRequest` (updated)

```python
class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    distinctId: str
    consent: bool = False
    polygon: str

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, value: str) -> str:
        # parse vertex count + bounding area; raise ValueError if either
        # check fails (see REQ-010/REQ-011 thresholds)
        ...
```

Exact validation-failure behavior: a `ValueError` raised inside a Pydantic `field_validator` surfaces as FastAPI's standard `422 Unprocessable Entity` — consistent with how `query`'s existing `reject_whitespace_only` validator behaves (see `tests/test_query.py::test_empty_query_returns_422`). This satisfies REQ-013's "clean 4xx" requirement without needing bespoke error-response handling in the router.

### Frontend area-mode state shape (new, in `MapView.tsx`)

```ts
type AreaMode = 'fixed' | 'draw'

type AreaState = {
  mode: AreaMode
  polygon: string       // WKT, always populated
  center: [number, number]
}
```

One setter function (e.g. `setArea(next: AreaState)`) is the single write path for: initial fixed selection, confirmed draw, redraw, and switch-to-Retiro (REQ-008).

### WKT conversion (new, ported from prototype)

Port `server_polygon.py`'s `polygon_points_to_wkt(points: list[[lat, lon]]) -> str` logic to the frontend (TypeScript), converting Leaflet's drawn-layer coordinates to a WKT string before submission — flipping `[lat, lon]` to `"lon lat"` pairs and closing the ring:

```python
# prototypes/scripts/server_polygon.py (existing, verified reference)
def polygon_points_to_wkt(points):
    ring = [(lon, lat) for lat, lon in points]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    coords = ",".join(f"{lon} {lat}" for lon, lat in ring)
    return f"POLYGON(({coords}))"
```

## 6. Implementation Mechanics

### Backend (`app/backend/`)

- `models/query.py`: add the required `polygon: str` field and a `field_validator` performing:
  1. Parse the WKT string into a list of `(lon, lat)` vertices (reuse/adapt `gbif_client.polygon_centroid()`'s ring-parsing logic, or extract a shared parsing helper both can call — avoid duplicating the WKT-parsing regex/string logic twice in the same service layer).
  2. Reject if vertex count (excluding the closing repeat) is below 3 (REQ-010/REQ-013).
  3. Compute the polygon's bounding-box area in km² (a simple equirectangular approximation using the bounding lat/lon extent is sufficient at this scale — no geodesic library needed, consistent with `polygon_centroid()`'s existing plain-average approach) and reject if it exceeds 25 km² (REQ-011/REQ-013).
- `routers/query.py`:
  - Remove the module-level `CENTER_LAT, CENTER_LON = polygon_centroid(GBIF_POLYGON)` constant.
  - Inside `submit_query()`, compute `center_lat, center_lon = polygon_centroid(body.polygon)` per request.
  - Pass `body.polygon` into `fetch_top_species(..., polygon=body.polygon)` (currently called with no `polygon` kwarg, relying on its default).
  - Pass the per-request `center_lat`/`center_lon` into `order_waypoints()` instead of the removed module constants.
  - The `GBIF_POLYGON` import from `services.gbif_client` is no longer needed in this router once the constant is removed (the frontend now owns sending the fixed-Retiro WKT string as `polygon` on fixed-mode requests) — confirm this before removing the import, and keep `GBIF_POLYGON` itself in `gbif_client.py` as the frontend's fixed-mode default source of truth (see Frontend section below) or duplicate its literal value as a frontend constant, whichever avoids a runtime cross-service fetch; either is acceptable, but the value must match exactly.
- `services/logging_client.py`: extend `log_query_submitted(query, distinct_id, polygon=None)` (or add the field to whichever log line REQ-015 targets) to include the polygon string in the `extra` payload.
- No changes required to `services/gbif_client.py` or `services/waypoints.py` (both already generalised per Verified Facts).

### Frontend (`app/frontend/`)

- Add `leaflet-draw` as an npm dependency (the prototype uses CDN-hosted `leaflet-draw@1.0.4`; production should install it via `npm install leaflet-draw` and import its CSS/JS rather than a CDN `<script>` tag, consistent with how `leaflet`/`react-leaflet` are already bundled). Check whether usable TypeScript types exist (`@types/leaflet-draw` or leaflet-draw's own types) before deciding between typed usage and a scoped `// @ts-expect-error`/local `.d.ts` shim — note this as an implementation-time check, not pre-decided here.
- `MapView.tsx`:
  - Add `AreaState` (see Interfaces) as component state, initialized to fixed mode / `RETIRO_CENTER` / the existing Retiro WKT constant.
  - Add a new presentational component (e.g. `AreaChoicePopup.tsx`) rendered conditionally over the map on initial load, implementing REQ-005/REQ-006/REQ-007.
  - Add a new component (e.g. `DrawAreaControl.tsx` or integrated directly) wrapping the Leaflet-draw toolbar, geolocation opt-in UI (REQ-001-REQ-004), and client-side confirm validation (REQ-010/REQ-011).
  - Extend the docked bar (either inside `QueryPanel.tsx` or a new sibling component) with the redraw/switch-to-Retiro actions (REQ-009), each calling the single area-mode setter (REQ-008).
  - The existing `/api/query` POST body gains `polygon: areaState.polygon`.
- `src/lib/posthog.ts`: no interface change needed — `trackEvent()` already accepts an event name and properties object; call it with the new area-mode event name/payload from `MapView.tsx` (REQ-016).

### Shared/cross-cutting

- Keep the WKT vertex/area validation logic conceptually identical on both sides (same thresholds: 3 vertices, 25 km²) even though the implementations are necessarily separate languages (Python backend, TypeScript frontend) — there is no shared-code mechanism between `app/backend/` and `app/frontend/` in this codebase today, so duplication here is accepted, not a defect.

## 7. Acceptance Criteria

- **AC-001**: Given the app loads fresh, when the map renders, then it is centered on Retiro Park at the existing default zoom, with a choice popup visible offering "Explore Retiro Park" and "Draw your own area."
- **AC-002**: Given the choice popup is showing, when the user selects "Explore Retiro Park," then the popup dismisses and the query form is immediately usable with the fixed Retiro polygon, matching current production behavior exactly.
- **AC-003**: Given the choice popup is showing, when the user selects "Draw your own area," then the popup dismisses, geolocation opt-in UI is shown (map not yet recentered), and the draw toolbar is active on the map.
- **AC-004**: Given draw mode is active and geolocation opt-in UI is showing, when the user clicks "Use my location" and grants permission, then the map recenters on their real position; when they deny/it errors, then an inline fallback message appears and the map stays at its current center — in both cases the draw tool remains usable.
- **AC-005**: Given the draw tool is active, when the user draws a polygon with fewer than 3 vertices, then the confirm control stays disabled; when they draw a polygon exceeding 25 km², then confirmation is blocked with an inline message; when they draw a valid polygon (≥3 vertices, ≤25 km²), then confirmation succeeds and the query form becomes usable.
- **AC-006**: Given a confirmed drawn polygon and a submitted query, when `/api/query` is called, then the request body includes the polygon as a WKT string, and the response's species/waypoints are computed relative to that polygon's own centroid, not Retiro's.
- **AC-007**: Given a result is showing in either mode, when the user clicks "Redraw area" (draw mode only), then the current polygon clears, the draw toolbar reappears at the map's current center (no recentering, no geolocation re-request).
- **AC-008**: Given a result is showing in draw mode, when the user clicks "Switch to Retiro," then the map recenters to Retiro and the fixed-mode polygon/query flow becomes active.
- **AC-009**: Given a `POST /api/query` request omitting the `polygon` field, when the request is validated, then it is rejected with a 422 response before any GBIF or LLM call is made.
- **AC-010**: Given a `POST /api/query` request with a `polygon` field that has fewer than 3 vertices or exceeds 25 km², when the request is validated, then it is rejected with a 422 response before any GBIF or LLM call is made.
- **AC-011**: Given any successful `/api/query` request, when the structured Cloud Run log line for query submission is emitted, then it includes the submitted polygon.
- **AC-012**: Given a user has given PostHog consent, when they select an area mode, redraw, or switch to Retiro, then a corresponding PostHog event fires; given consent has not been given, no such event fires (matching existing consent-gating behavior elsewhere in the app).
- **AC-013**: Given a page refresh at any point in the draw flow (before or after results), when the page reloads, then the app returns to the initial choice popup with no drawn polygon retained.

## 8. Test Strategy

Full TDD per `/tdd` and `/testing` — this is production code, the project's light prototype-only testing convention does not apply.

**Backend unit tests** (new, likely `app/backend/tests/test_models_query.py` or extending `tests/test_query.py`):
- WKT-to-vertex-count parsing: valid closed ring, valid unclosed ring (auto-closed), exactly 3 vertices (pass), 2 vertices (fail).
- Bounding-area calculation: a known small polygon well under 25 km² (pass), a known polygon just under the threshold (pass), just over the threshold (fail), and the existing fixed Retiro polygon itself (must pass — it's well under 25 km²; verify this as a sanity check that the cap doesn't accidentally break the existing default area).
- `QueryRequest` validation: missing `polygon` field → `ValidationError`; malformed WKT string → `ValidationError`.

**Backend integration tests** (extending `app/backend/tests/test_query.py`, using the existing `TestClient`/mocking conventions from `tests/conftest.py`):
- `POST /api/query` with a valid non-Retiro custom polygon (GBIF client mocked at the existing service-layer boundary) returns a `resolved`/`no_results` outcome computed from that polygon's own centroid, not Retiro's.
- `POST /api/query` omitting `polygon` returns 422.
- `POST /api/query` with a too-small (< 3 vertices) or too-large (> 25 km²) polygon returns 422, and — assert explicitly — the mocked GBIF/LLM service functions were never called (proving REQ-013's "before any GBIF/LLM cost" ordering, not just the final status code).

**Live eval test** (new, in `app/backend/tests/evals/`, `@pytest.mark.eval`):
- Run the real pipeline against a real non-Retiro polygon (adapt `prototypes/reference/rascafria_area.geojson`, converting its GeoJSON coordinates to the WKT format `/api/query` now expects) with a real query (e.g. "show me some plants"), asserting a real `resolved` or `no_results` outcome and that returned species hotspots fall within a sane distance of that polygon's own centroid (not Retiro's) — following the same live-verification pattern as `test_full_pipeline_eval.py`.

**Frontend component tests** (Vitest/RTL, new files alongside existing `app/frontend/src/components/`):
- `AreaChoicePopup`: renders both options on mount; selecting each calls the expected callback/sets the expected mode.
- Draw-mode confirm gating: confirm disabled under 3 vertices, disabled over the 25 km² cap, enabled for a valid polygon (mock the Leaflet-draw layer's vertex data directly rather than driving real map drawing — Leaflet doesn't run cleanly in jsdom).
- Docked bar: "Redraw area" clears polygon state without changing center; "Switch to Retiro" resets to fixed mode and Retiro center; both only call the single area-mode setter (assert via a mock/spy that no divergent code path exists for the two).
- Geolocation opt-in: mock `navigator.geolocation.getCurrentPosition` for both the success and error/denied callback paths, asserting the correct UI state in each (recentered map vs. inline fallback message, draw tool usable in both).

**Browser smoke test** (extend `tests/e2e_web_smoke_test.py`):
- New scripted flow: load the app, assert the choice popup appears, select "Draw your own area," draw a real small polygon via `agent-browser`'s browser automation (a fixed set of click coordinates or a scripted drag sequence — small, valid, real triangle/quadrilateral over an area with known GBIF data, e.g. reuse or adapt the Rascafría reference area), confirm it, submit a query, and assert a real resolved/no_results outcome renders — mirroring the existing fixed-Retiro assertions (marker count vs. species count, route-line presence, results panel content) but for the drawn-area path. This is the final go/no-go gate before this slice is considered done, per this project's established pattern from Slice 3's production rollout.

## 9. Rationale & Context

- **Single code path via a required `polygon` field** (REQ-012) rather than an optional field with a backend default: this was an explicit design decision reached in this session's `/grill-me` — the user wanted fixed-Retiro and draw-your-own to be "just passing in polygon coordinates," not two architecturally separate flows, so the backend no longer owns a notion of a "default" at all; the frontend always supplies one explicitly.
- **Map renders first, popup overlaid on top** (REQ-005) rather than a blank choice screen before any map appears: explicit user preference in this session — starting on a visible, pannable map immediately communicates "this is a navigable map experience" rather than requiring a click through an unexplained intermediate screen first.
- **Geolocation gated behind explicit UI, not fired on load** (REQ-001, REQ-002): matches this project's existing consent-conscious posture (the separate `ConsentBanner`/PostHog opt-in pattern) even though geolocation itself needs no consent banner — an unexplained native browser permission prompt on load was judged intrusive and untrustworthy, especially given the explicit intent to reassure users this is a different, non-stored pathway from analytics consent.
- **Redraw and switch-to-Retiro as separate, explicitly labeled actions** (REQ-009) rather than folded into one "change area" button: initially proposed as a single combined action to minimize the docked bar's control count, but rejected once the user flagged that "redraw" implicitly means "let me try again roughly here," and silently relocating the user's map view to a fixed park across the world under a "redraw" label would be confusing. The added implementation cost of a third button was judged low (one more call to the same underlying setter), so the clearer, distinct-action design was chosen.
- **25 km² area cap** (REQ-011): a round number proposed and agreed as "large enough to cover a large walking area" without being large enough to create GBIF fetch-volume/cost exposure comparable to the already-documented unfiltered-birds-in-Retiro case (55,756 occurrences). Not derived from a formal cost model — treated as a reasonable starting guardrail, revisitable once real usage data exists.
- **No `localStorage` persistence** (REQ-017): kept consistent with the app's existing no-server-session-state design (the same reasoning documented for why `/gbif-species-query`'s prototype design was stateless — see the shareable-walk-link backlog entry in `docs/FEATURE_IDEAS_BACKLOG.md`) — redrawing is cheap, and a "restore my last drawn area" feature is better served later by the already-backlogged shareable-walk-link idea than by ad hoc client persistence now.
- **Self-intersecting polygons deferred** (CON-003): GBIF's actual behavior on invalid WKT geometry (reject cleanly vs. silently misinterpret) is not yet known and wasn't worth guessing at or guarding against speculatively; flagged as a concrete follow-up investigation instead.

## 10. Dependencies & External Integrations

- **`leaflet-draw`** (new frontend dependency): the polygon-drawing toolbar, validated in prototype at version `1.0.4`. Needs adding to `app/frontend/package.json` and bundling (not CDN-loaded, unlike the prototype) to match this project's existing dependency conventions.
- **Browser Geolocation API** (`navigator.geolocation`): no new package — a standard browser API, used client-side only.
- **GBIF `occurrence/search`**: unchanged integration — already accepts an arbitrary `geometry` (WKT) parameter; this slice just varies which polygon is sent per request instead of always sending the fixed constant.
- **`agent-browser` CLI**: already a dependency of the existing smoke-test tooling; the extended smoke test (Test Strategy) needs it to be capable of scripted drag/click sequences to draw a polygon on a real map — confirm this capability exists before committing to the exact drawing-automation approach; if `agent-browser` can't reliably drive a multi-point map draw gesture, this may need a fallback automation approach flagged as an implementation-time risk, not a blocker for the rest of the slice.

## 11. Examples & Edge Cases

- **Exactly-3-vertex polygon**: must be accepted (the minimum, not an off-by-one exclusion) — both client- and server-side.
- **Polygon exactly at the 25 km² boundary**: implementation should define this as inclusive-pass or inclusive-fail consistently between client and server (recommend: `area_km2 <= 25` passes, `> 25` fails, applied identically on both sides) — pick one and test it explicitly rather than leaving the boundary condition implicit.
- **The existing fixed Retiro polygon itself, run through the new validation**: must pass cleanly (sanity-check regression — see Test Strategy's backend unit tests) since it's now a `polygon` value submitted like any other, not backend-exempt.
- **Geolocation permission previously denied at the OS/browser level (not just this session)**: `getCurrentPosition()`'s error callback fires the same as an in-session denial — REQ-004's handling covers this without needing separate logic.
- **User draws a polygon, then draws a second one without clearing the first** (Leaflet-draw's default multi-shape behavior): out of scope to specify precisely here — recommend the draw tool be configured to allow only one active shape at a time (clear/replace on new draw start), consistent with the prototype's single-polygon confirm flow, but confirm this is Leaflet-draw's actual default/configurable behavior during implementation rather than assuming it.
- **Rascafría reference polygon** (`prototypes/reference/rascafria_area.geojson`): a real, previously-validated non-Retiro area — reuse for the live eval test and potentially the smoke test, converting its GeoJSON `[lon, lat]` coordinates to WKT (no axis flip needed, per Verified Facts).

## 12. Validation Criteria

This slice is correctly implemented when:
- All acceptance criteria (Section 7) pass.
- All five test layers (Section 8) are green, including a live, headed run of the extended `tests/e2e_web_smoke_test.py` against local dev, then re-run against the deployed Cloud Run URL post-deploy (matching the validation pattern used for Slice 3's production rollout per `docs/status_docs/WORK_SUMMARY_100826.md`).
- `ruff check .` and `mypy .` (backend) and the frontend lint/typecheck pass locally before opening a PR — called out explicitly per the process gap flagged in the prior session's work summary (lint/typecheck failures were previously only caught in CI, not locally).
- Manual verification: a real polygon drawn by hand in a local dev browser, in a genuinely different real-world location from Retiro, produces a walk with waypoints visibly within that drawn area on the map (not Retiro's).
- `ARCHITECTURE.md` is updated to reflect the new `polygon` field on `/api/query`, the removal of the backend's fixed-default fallback, and draw-mode's presence in `MapView.tsx` — per this project's standing instruction to keep it current when a change alters what it describes.

## 13. Related Specs / Further Reading

- `docs/prds/nature-quest-prd-300726.md` — parent PRD; Story 5 and the Technical Slices table (Slice 9) this spec implements.
- `docs/decisions/ADR-011-multi-taxon-query-resolution-strategy.md` — establishes the "no protection may rely on an attacker not reading the code" posture applied here to polygon validation.
- `prototypes/README.md` — authoritative map of what's been prototyped and verified, including the polygon-drawing prototype this spec builds on.
- `docs/status_docs/WORK_SUMMARY_100826.md` — most recent session; flags this slice as next priority and documents the `polygon_centroid()` generalisation this spec depends on.
- `docs/status_docs/WORK_SUMMARY_250726.md` — GeoJSON polygon-file prototype findings, Rascafría reference area provenance.
- `docs/FEATURE_IDEAS_BACKLOG.md` — the shareable-walk-link entry referenced in this spec's rationale for deferring polygon persistence.
