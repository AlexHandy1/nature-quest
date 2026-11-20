---
title: Single-world map bounds + structured API errors + frontend error fallback
version: 1.0
date_created: 2026-11-20
last_updated: 2026-11-20
tags: [tool]
status: Design complete, not yet built
sources:
  - app/frontend/src/components/MapView.tsx (production)
  - app/frontend/src/components/DrawAreaControl.tsx (production)
  - app/frontend/src/components/QueryPanel.tsx (production)
  - app/frontend/src/components/NarrationControl.tsx (production)
  - app/frontend/src/lib/polygon.ts (production)
  - app/frontend/src/components/MapView.test.tsx (production test patterns)
  - app/backend/models/query.py (production)
  - app/backend/services/species_index.py (production)
  - app/backend/services/rate_limiter.py (production — existing structured-error pattern)
  - app/backend/main.py (production — exception handler registration)
  - app/backend/tests/test_models_query.py, test_query.py (production test patterns)
  - app/frontend/node_modules/leaflet/src (Leaflet 1.9.4 source — verified behaviour below)
  - Production log, 2026-11-07, Toowoomba (Australia) ValueError from pygeohash
  - Local reproduction session, 2026-11-19/20 (agent-browser against local Vite + stub backend)
  - docs/specs/spec-tool-draw-your-own-area-100826.md (parent flow)
---

# Introduction

This spec fixes a production bug where "draw your own area" fails for some
locations (first reported in Toowoomba, Australia), and the follow-on UX
problem: when a request fails, the app hangs on "Searching…" with no message.
It has three parts, built in this order:

- **A2:** lock the map to a single, non-repeating world, so drawn coordinates
  are always real longitudes.
- **D:** make the backend reject invalid coordinates cleanly, and return
  structured JSON errors for validation failures and unexpected exceptions.
- **E:** make the frontend handle any non-success response, including
  non-JSON bodies and network failures, with a clear fallback message. It
  must never leave the UI stuck in a loading state.

This document is meant to be implementable by an agent with no other context.
Read it fully before writing code.

## 1. Purpose & Scope

**In scope**
- A2: Leaflet map options in `MapView.tsx` so the user cannot reach a
  repeated "world copy" (`maxBounds`, `maxBoundsViscosity`, `noWrap`).
- D1: latitude/longitude range validation on `QueryRequest.polygon`.
- D2: a `RequestValidationError` handler that returns a structured 422 body.
- D3: a catch-all handler for unhandled exceptions that returns a structured
  500 JSON body and logs the exception server-side.
- E: robust response handling in `MapView.handleSubmit` (`/api/query`) and
  `NarrationControl.handleGenerate` (`/api/narrate`), which share the same
  failure shape.
- Updating `ARCHITECTURE.md` where it describes the map or error handling.

**Non-goals**
- **Normalising longitudes in the frontend (Option B) or backend (Option C).**
  Considered and rejected; see §9.
- **Polygons that cross the antimeridian (±180°, e.g. Fiji).** Once A2 is in
  place they can't be drawn. D1 rejects them if they ever arrive. Real
  support (splitting the bbox in `candidate_cells`, plus the centroid and area
  maths) is deferred.
- **`worldCopyJump` (Option A1).** Not used. It does not cover the zoom
  route (see §2, VF-4), and A2 makes it redundant.
- **Fixing local dev without a PostHog token.** `distinctId` arrives `null`,
  which gives a 422 locally. That's a separate issue; after this spec it at
  least shows a message instead of a blank.

## 2. Verified Facts

- **VF-1 Root cause.** Leaflet does not wrap longitudes into [-180, 180].
  On a repeated world copy, Toowoomba (≈151.96°E) is reported as 511.96
  (+360) or ≈ -208 (−360).
  `DrawAreaControl.layerToPoints` passes these raw values to
  `pointsToWkt`. `QueryRequest.validate_polygon` checks only vertex count and
  bounding area, so it accepts them. `candidate_cells` then calls
  `pgh.encode(lat, lon)`, which raises `ValueError: Longitude must be between
  -180.0 and 180.0 degrees, but got 511.97…`, and the request returns HTTP 500.
  *Source: production log 2026-11-07; reproduced locally both directly
  (`candidate_cells` on a +360-shifted polygon) and end to end via
  agent-browser.*
- **VF-2 Browser reproduction.** In the local repro: zoom out to minimum zoom
  (centre −3.68), drag one world-width east (centre 356.48, visually
  identical), scroll-zoom into Toowoomba (centre 511.96, −27.56), draw a
  polygon around Queens Park, submit "birds". The backend received
  `POLYGON((511.958 -27.558, …))` and `candidate_cells` raised the same error.
  An earlier, less controlled drag put the map centre at longitude 2938. The
  map is not bounded today.
- **VF-3 The "hang" mechanism.** `MapView.handleSubmit` calls
  `await response.json()` unconditionally and sets `loading=false` only
  after that. An unhandled FastAPI 500 returns the plain-text body
  `Internal Server Error`, so `.json()` throws, `setLoading(false)` is never
  reached, and the UI stays on "Searching…". A rejected `fetch` (network
  failure) hangs the same way. `NarrationControl.handleGenerate` has the same
  shape (`setStatus('loading')`, then an unguarded `.json()`).
  *Source: production code. The plain-text 500 body is Starlette's default
  for unhandled exceptions.*
- **VF-4 `worldCopyJump` only applies to drag and keyboard panning.** In
  Leaflet 1.9.4 the option is read only in `map/handler/Map.Drag.js`
  (`_onPreDragWrap`) and `map/handler/Map.Keyboard.js`. Scroll-wheel zoom,
  double-click zoom and `setView` never wrap. At zoom 2 on a 1280px-wide
  viewport centred on Madrid, the visible range is about −229 to +221
  longitude, so a second copy of eastern Australia (Toowoomba ≈ −208) is
  visible and can be zoomed into without dragging.
- **VF-5 `maxBounds` clamps every view change.** `Map.setView` calls
  `_limitCenter(center, zoom, options.maxBounds)` (Map.js:181).
  Scroll-wheel and double-click zoom both go through `setZoomAround`, which
  calls `setView`. Dragging is constrained by `maxBoundsViscosity`
  (Map.Drag.js:100), and `_panInsideMaxBounds` runs on `moveend`. So
  `maxBounds` covers drag, zoom, keyboard and programmatic moves, including
  `MapRecenter`.
- **VF-6 Structured-error pattern already exists.**
  `services/rate_limiter.handle_rate_limit_exceeded` returns
  `{"error": "rate_limited", "message": "<user-facing copy>"}` with status
  429, registered in `main.create_app` via `app.add_exception_handler`. The
  frontend reads `body.status ?? body.error` as the `Outcome`, and
  `body.message` as display text. *Source: production code.*
- **VF-7 Today's 422 body is unrenderable.** FastAPI's default validation
  error body is `{"detail": [...]}`, with no `error` or `message` fields. The
  frontend gets `outcome === undefined` and renders an empty
  `form-message--undefined` paragraph. Existing backend tests assert only
  `status_code == 422` on validation failures, not the body shape
  (`tests/test_query.py`). *Source: production code and tests.*
- **VF-8 Ordinary polygons are unaffected.** Any polygon drawn on the
  primary world (e.g. Hyde Park, lon −0.19…−0.15; Retiro, lon ≈ −3.68)
  already has in-range coordinates. None of A2, D or E changes the request
  for these.
- **VF-9 The draw flow isn't covered by automated e2e.** The flow was removed
  from `tests/e2e_web_smoke_test.py` on 2026-08-12 as flaky (Leaflet-draw via
  synthetic mouse events). The 2026-11-20 manual agent-browser repro
  succeeded by attaching to a manually launched Chrome over CDP (see §12).

## 3. Definitions

- **World copy:** a repeated rendering of the world, side by side, that
  Leaflet shows when zoomed out. Coordinates on a copy are offset by
  multiples of 360°.
- **Antimeridian:** the ±180° longitude line.
- **`maxBounds` / `maxBoundsViscosity`:** Leaflet options that restrict the
  viewable area. Viscosity 1.0 makes the bounds fully solid while dragging.
- **`noWrap`:** a TileLayer option that stops tiles from being drawn beyond
  the single world.
- **Structured error body:** `{"error": <code>, "message": <user-facing
  string>}`, matching the existing rate-limit response (VF-6).

## 4. Requirements, Constraints & Guidelines

**A2: single-world map**
- **REQ-001**: `MapContainer` sets
  `maxBounds={[[-90, -180], [90, 180]]}` and `maxBoundsViscosity={1.0}`.
  Define the bounds as a module-level constant (`WORLD_BOUNDS`), with a
  comment explaining it prevents world copies, so drawn longitudes are always
  in [-180, 180].
- **REQ-002**: `TileLayer` sets `noWrap`, so no repeated tiles render.
- **REQ-003**: Any area drawn on the map, reached by any combination of drag,
  scroll-zoom, double-click zoom, zoom buttons, keyboard or "Use my
  location", produces WKT with every longitude in [-180, 180].
- **REQ-004**: `MIN_ZOOM`. [NEEDS INPUT: keep at 2, or raise to 3? At zoom 2
  the world is 1024px wide, so wider viewports show grey margins beside it.
  Recommendation: decide from an agent-browser screenshot at 1280px and
  1920px during implementation. Raise to 3 only if the margins look broken.
  Either value is functionally correct.]

**D: backend**
- **REQ-010**: `QueryRequest.validate_polygon` rejects any vertex with
  latitude outside [-90, 90] or longitude outside [-180, 180] with a
  `ValueError`, so the request returns a 422. The check runs before the area
  check and before any LLM or DuckDB call.
- **REQ-011**: Register a `RequestValidationError` handler in
  `main.create_app` that returns status 422 with
  `{"error": "invalid_request", "message": INVALID_REQUEST_MESSAGE}`.
  It applies to all routes (`/api/query` and `/api/narrate`).
- **REQ-012**: Register a catch-all handler for unhandled `Exception` that
  returns status 500 with
  `{"error": "internal_error", "message": INTERNAL_ERROR_MESSAGE}`, and logs
  the exception with its traceback through the existing structured logging.
- **CON-010**: Neither the 422 nor the 500 body may include exception text,
  tracebacks, validator internals or the submitted payload. The user-facing
  `message` is a fixed constant. Details go only to server logs.
- **CON-011**: Do not change existing 429 responses (`rate_limited`,
  `daily_limit_reached`) or any 200-status outcome bodies.
- **GUD-010**: Where the request body is parseable, also record the outcome
  with the existing outcome logging (`log_query_outcome` /
  `log_narration_outcome`, outcomes `invalid_request` / `internal_error`),
  following `handle_rate_limit_exceeded`. The handler must not fail if the
  body is missing or isn't valid JSON.

**E: frontend**
- **REQ-020**: Wrap `MapView.handleSubmit` so that every path (success,
  structured error, non-JSON body, rejected `fetch`) ends with `loading` set
  to `false`. Use `try`/`catch`/`finally`.
- **REQ-021**: On a non-OK response, use `body.error` as the outcome and
  `body.message` as display text when the body parses and has both.
  Otherwise fall back to outcome `error` with `GENERIC_ERROR_MESSAGE`.
- **REQ-022**: On a rejected `fetch` or an unparseable body, show outcome
  `error` with `GENERIC_ERROR_MESSAGE`, clear any species and markers from a
  previous result, and keep the drawn area and query text so the user can
  retry.
- **REQ-023**: Extend the `Outcome` type with `invalid_request`,
  `internal_error` and `error`, all mapped to the `'error'` variant in
  `VARIANT_BY_OUTCOME`. Any unknown outcome string still renders as an error
  with a non-empty message, never as `form-message--undefined` or a blank.
- **REQ-024**: Give `NarrationControl.handleGenerate` the same treatment:
  `status` always leaves `'loading'`, and `errorMessage` falls back to its
  existing `'Something went wrong.'` when the body is missing or
  unparseable.
- **REQ-025**: Keep firing `trackEvent('query_outcome', { status })` on
  failures, with the resolved outcome (`error`, `internal_error`, …), so the
  failure rate shows up in PostHog.
- **PAT-020**: Put response parsing in one small helper in `src/lib/`
  (e.g. `readJsonSafely(response)`, which returns the parsed body or `null`)
  and use it from both call sites, instead of duplicating `try`/`catch`
  around `.json()`.

## 5. Interfaces & Data Contracts

**422 (validation failure: any route, including out-of-range coordinates)**
```json
{ "error": "invalid_request",
  "message": "Something about that request wasn't valid — try redrawing your area or rephrasing your search." }
```

**500 (unhandled exception: any route)**
```json
{ "error": "internal_error",
  "message": "Something went wrong on our side — please try again in a moment." }
```

**Frontend fallback: non-JSON body or network failure (no server body)**
- outcome `error`, message:
  `"We couldn't reach Nature Quest just now — please check your connection and try again."`

Copy wording may be refined during implementation. The codes are the
contract.

**Unchanged:** 200 bodies (`resolved`, `unresolved`, `no_results`) and 429
bodies (`rate_limited`, `daily_limit_reached`).

**Updated frontend `Outcome` type**
```ts
export type Outcome =
  | 'resolved' | 'unresolved' | 'no_results'
  | 'rate_limited' | 'daily_limit_reached'
  | 'invalid_request' | 'internal_error' | 'error'
```

## 6. Implementation Mechanics

Build as three commits in order, each test-first:

1. **E (frontend fallback) first.** This turns any future failure,
   including today's bug, into a visible message. It's independently
   shippable.
   - New `src/lib/http.ts` (`readJsonSafely`) plus a test.
   - Edit `MapView.tsx` (`handleSubmit`), `QueryPanel.tsx` (`Outcome`,
     `VARIANT_BY_OUTCOME`, unknown-outcome fallback) and
     `NarrationControl.tsx` (`handleGenerate`).
2. **D (backend).**
   - `models/query.py`: add range checks to `validate_polygon`.
   - New `services/error_handlers.py`, holding
     `handle_request_validation_error`, `handle_unhandled_exception` and the
     two message constants. Mirror `services/rate_limiter.py`'s handler style.
   - `main.create_app`: register both handlers next to the existing
     `RateLimitExceeded` handler, reusing the same `# type: ignore[arg-type]`
     comment.
3. **A2 (map bounds).**
   - `MapView.tsx`: add the `WORLD_BOUNDS` constant, the `maxBounds` and
     `maxBoundsViscosity` props on `MapContainer`, and `noWrap` on
     `TileLayer`.
   - Resolve REQ-004 (`MIN_ZOOM`) from screenshots.
4. **Docs.** Update `ARCHITECTURE.md`: the map is bounded to a single world,
   and API errors use structured `{error, message}` bodies with a frontend
   fallback. Note the antimeridian limitation.

No new dependencies.

## 7. Acceptance Criteria

- **AC-001**: Given the map at minimum zoom, when the user drags or
  scroll-zooms anywhere, then the map centre longitude stays within
  [-180, 180] and no repeated world tiles render.
- **AC-002**: Given the user reaches Toowoomba by any route (dragging east,
  dragging west, or scroll-zooming from the world view), when they draw a
  polygon and submit "birds", then the request polygon has longitudes
  ≈ 151.9x and the request does not fail.
- **AC-003**: Given a polygon drawn around a UK or Madrid park on the normal
  map, when submitted, then the request WKT is identical to today's
  (no regression).
- **AC-004**: Given a `/api/query` body with any longitude outside
  [-180, 180] or latitude outside [-90, 90], then the response is 422 with
  `error: "invalid_request"`, and no LLM or DuckDB call is made.
- **AC-005**: Given any request that fails validation (e.g. empty query),
  then the 422 body has `error` and `message` keys and no `detail` internals.
- **AC-006**: Given an unhandled exception inside a route, then the
  response is 500 JSON with `error: "internal_error"` and the fixed
  message, and the server log contains the traceback.
- **AC-007**: Given `/api/query` returns a plain-text 500, or `fetch`
  rejects, when the user submits, then within the same tick the UI leaves
  "Searching…", shows a non-empty error message, re-enables the input and
  button, and keeps the drawn area.
- **AC-008**: Given `/api/narrate` returns a plain-text 500 or `fetch`
  rejects, then the narration control leaves the loading state and shows an
  error message.
- **AC-009**: Given any structured error body, then the UI shows that
  body's `message` with the error styling.

## 8. Test Strategy

Full TDD per `/tdd` and `/testing`: red, then green, then refactor, one
behaviour at a time.

**Backend (pytest, `app/backend/tests/`)**
- `test_models_query.py`:
  - polygon with longitude 511.95 → ValidationError;
  - longitude −208.04 → ValidationError;
  - latitude 91 → ValidationError;
  - longitude exactly ±180 and latitude exactly ±90 → accepted (boundaries
    are inclusive);
  - existing Retiro polygon is still accepted.
- `test_query.py`:
  - out-of-range polygon → 422 with `error == "invalid_request"` before any
    LLM or DuckDB call (mirror
    `test_missing_polygon_returns_422_before_any_llm_or_duckdb_call`);
  - existing empty-query and missing-polygon tests also assert the new body
    shape.
- New `test_error_handlers.py`:
  - a route forced to raise (patch a dependency, such as
    `resolve_taxon_filters`, with `side_effect=RuntimeError`) → 500 JSON
    `internal_error`, the body doesn't contain the exception text, and the
    log captured via `caplog` contains the traceback;
  - a validation error on `/api/narrate` gets the same 422 shape;
  - a non-JSON request body doesn't crash the validation handler.
- The existing 429 tests (`test_query_rate_limit.py`, `test_query_budget.py`)
  must pass unchanged.

**Frontend (Vitest + Testing Library, `app/frontend/src/`)**
- `lib/http.test.ts`: `readJsonSafely` returns the parsed object for a JSON
  body and `null` for a plain-text body.
- `MapView.test.tsx`, following the existing `vi.stubGlobal('fetch', …)`
  pattern:
  - plain-text 500 (`new Response('Internal Server Error', {status: 500})`)
    → error message shown, the "Create walk" button is enabled, and
    "Searching…" is gone;
  - `fetch` rejects → same;
  - structured 422 → that body's message is shown;
  - structured 500 → that body's message is shown;
  - `trackEvent` is called with the failure outcome.
- `MapView.test.tsx`, A2: extend the `react-leaflet` mock so `MapContainer`
  and `TileLayer` expose their props (e.g. as `data-*` attributes), then
  assert that `maxBounds` equals the world bounds, `maxBoundsViscosity` is
  1, and `noWrap` is set. Leaflet's runtime clamping (VF-5) is library
  behaviour, so check it in the browser (§12), not in unit tests.
- `QueryPanel.test.tsx`: each new outcome renders with the error variant;
  an unknown outcome renders an error with a non-empty message.
- `NarrationControl.test.tsx`: plain-text 500 and rejected `fetch` both
  leave the loading state and show an error.

**E2E / practical validation:** see §12. Don't re-add the draw flow to
`tests/e2e_web_smoke_test.py` in this slice (VF-9 flakiness). Record the
manual agent-browser run in the session's work summary instead.

## 9. Rationale & Context

Options were compared in the 2026-11-19/20 session:

- **A2 was chosen over B** (shifting drawn polygons in the frontend). A2
  removes the cause, since world copies are unreachable. It's a few lines
  of configuration, with no coordinate maths and no state handling for
  moving drawn layers between copies. The costs are a hard map edge at the
  Pacific, grey margins at low zoom on wide screens, and no antimeridian
  drawing. All were judged acceptable for this app.
- **A1 (`worldCopyJump`) was rejected as the fix.** It does not cover the
  scroll-zoom route (VF-4), so it would make the bug rarer, not fix it.
- **C (normalising in the backend) was rejected.** Result coordinates are
  in canonical longitudes, and the map would stay on the copy, so markers
  would render off screen. Users would see an empty map instead of an error,
  and the logs would show a success, hiding the regression.
- **D rejects rather than wraps**, so that any future coordinate regression
  shows up as a logged 422, instead of being silently repaired or crashing
  as a 500.
- **D's catch-all handler and E's fallback are deliberately general.** The
  user-facing problem was not only this bug but the silent hang that any
  failure produces (VF-3). Together they make every failure path end in a
  visible message.

## 10. Dependencies & External Integrations

- Leaflet 1.9.4 / react-leaflet 5 (existing). `maxBounds`,
  `maxBoundsViscosity` and `noWrap` are stable options in this version.
- FastAPI / Starlette exception handlers (existing pattern).
- Existing structured logging (`services/logging_client.py`) and PostHog
  `trackEvent`.
- No new external services.

## 11. Examples & Edge Cases

| Case | Before | After |
|---|---|---|
| Toowoomba via world copy (511.96) | 500, UI stuck on "Searching…" | Unreachable on the map; if sent directly: 422 `invalid_request` with a message |
| Toowoomba via the left copy (−208) | 500, stuck | Unreachable; 422 if sent directly |
| Hyde Park / Retiro on the normal map | Works | Identical request, works |
| Fiji, straddling 180° | 500 or misleading area error | Can't be drawn; 422 if sent directly (documented limitation) |
| Click in the grey margin at zoom 2 | n/a | A polygon that small can't be drawn validly at that zoom given the area cap; D1 rejects it anyway |
| Backend crash (any cause) | Plain-text 500, UI stuck | JSON 500, message shown, input re-enabled |
| Network drop mid-request | `fetch` rejects, UI stuck | Connection message shown, input re-enabled |
| Local dev without PostHog (`distinctId` null) | 422, blank message | 422, `invalid_request` message shown |

## 12. Validation Criteria

1. All backend and frontend test suites pass (`pytest`, `npm test`), and
   lint and type checks are clean.
2. **Agent-browser check** against the local Vite dev server and backend
   (the real backend, or the scratch stub that runs the real
   `QueryRequest` and `candidate_cells`):
   - Zoom to minimum and try to drag a full world-width east and west. The
     centre longitude stays in [-180, 180] and no repeated tiles appear.
   - Scroll-zoom to Toowoomba from the world view, draw around Queens Park,
     submit. The request WKT shows ≈ 151.9x and there's no 500.
   - Repeat for a UK park. The request WKT is unchanged from before.
   - Force a plain-text 500 from the stub. The UI shows the error and
     re-enables the input.
   - Screenshot at 1280px and 1920px to resolve REQ-004.
   - **Container note:** Chrome in this dev container needed
     user-extracted system libraries (`LD_LIBRARY_PATH`) and
     `--no-sandbox --disable-dev-shm-usage`. It was launched manually and
     agent-browser attached with `--cdp <port>`. A permanent fix belongs in
     the dev image (`agent-browser install --with-deps` as root, plus
     `AGENT_BROWSER_ARGS`).
3. After deploy: confirm the Toowoomba flow on the live app, and check that
   no further `pygeohash` `ValueError` appears in Cloud Run logs.

## 13. Related Specs / Further Reading

- `docs/specs/spec-tool-draw-your-own-area-100826.md`: the parent
  draw-your-own-area flow and polygon validation posture.
- `docs/decisions/ADR-012-area-selection-persistent-widget-not-popup-funnel.md`
- `ARCHITECTURE.md`: update per §6 step 4.
- Leaflet 1.9.4 source: `src/map/Map.js` (`setView`, `_limitCenter`),
  `src/map/handler/Map.Drag.js`, `src/layer/tile/GridLayer.js` (`noWrap`).
