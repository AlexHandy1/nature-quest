# Work Summary — 20 November 2026 (parallel auto session)

Branch: `aus_coords_bug`. This was a bug-investigation and planning session,
covering 19–20 Nov. No production code was changed.

## What was built

- `docs/specs/spec-tool-map-world-bounds-and-error-fallback-201126.md`
  (commit `b23ec0e`) is the implementable spec for the chosen fix:
  - **A2:** a single-world Leaflet map (`maxBounds`,
    `maxBoundsViscosity: 1.0`, `noWrap`);
  - **D:** backend lat/lon range validation, plus structured `{error,
    message}` bodies for 422 validation failures and a catch-all 500 handler;
  - **E:** frontend fallback so every failure path shows a message and
    clears the loading state, in both `MapView.handleSubmit` and
    `NarrationControl.handleGenerate`.
  - Build order: E, then D, then A2, then the `ARCHITECTURE.md` update.
- `docs/status_docs/agent_container_updates_201126.md` (commit `1edcef7`)
  sets out the proposed `agent-container` Dockerfile and compose changes to
  support agent-browser. They're untested, because the image couldn't be
  rebuilt from inside the container.
- Scratch only, not committed:
  `/tmp/claude-1001/-workspace-nature-quest/7b9e3580-6767-4a3f-b388-d00139fe84bc/scratchpad/stub_backend.py`.
  It's a FastAPI stub that runs the real `QueryRequest` validation and the
  real `candidate_cells()`, skipping only the LLM call and the parquet
  lookup.

## What was explored / learnt

- **Production bug, root cause confirmed.** The production log (2026-11-07,
  Toowoomba) showed `ValueError: Longitude must be between -180.0 and 180.0
  degrees, but got 511.97507858276373` raised from `pygeohash.encode`.
  - Leaflet does not wrap longitudes. On a repeated world copy, Toowoomba
    (≈151.96) is reported as 511.96 (+360) or ≈ −208 (−360).
  - `DrawAreaControl.layerToPoints` → `pointsToWkt` sends these raw values.
  - `QueryRequest.validate_polygon` checks only vertex count and bounding
    area, so out-of-range coordinates reach `candidate_cells` (`pgh.encode`)
    and cause a 500.
  - It isn't Australia-specific: any location reached through a world copy
    fails. Distant locations are just more likely to be reached that way
    from the Madrid default view.
- **Reproduced locally in two ways:**
  - Directly: a +360-shifted Toowoomba polygon passes `QueryRequest`, then
    `candidate_cells` raises the same error. The unshifted polygon gives 4
    cells.
  - End to end in agent-browser: zoom out to zoom 2 (centre −3.68), drag
    one world-width east (centre 356.48, visually identical), scroll-zoom
    into Toowoomba (centre 511.96, −27.56), draw around Queens Park, submit
    "birds". The backend received `POLYGON((511.958 -27.558, …))`, got the
    same `ValueError`, and returned 500.
  - An earlier, uncontrolled drag put the map centre at longitude 2938.
- **The "UX hang" mechanism.** `MapView.handleSubmit` calls
  `await response.json()` unconditionally, before `setLoading(false)`.
  Starlette's unhandled 500 is plain text (`Internal Server Error`), so
  `.json()` throws and the UI is stuck on "Searching…".
  `NarrationControl.handleGenerate` has the same shape. FastAPI's default
  422 body (`{"detail": [...]}`) has no `error` or `message`, so it renders
  an empty `form-message--undefined` paragraph.
- **Leaflet 1.9.4 source, verified:**
  - `worldCopyJump` is only read in `Map.Drag.js` (`_onPreDragWrap`) and
    `Map.Keyboard.js`, so scroll-wheel zoom, double-click zoom and `setView`
    never wrap. It doesn't cover the zoom route: at zoom 2 a second copy of
    eastern Australia (Toowoomba ≈ −208) is visible at the left edge.
  - `maxBounds` clamps all view changes: `setView` → `_limitCenter`
    (Map.js:181), and scroll and double-click zoom go through
    `setZoomAround` → `setView`.
- **Option B (shift the polygon by k×360 based on its centre) is a no-op
  for normal polygons.** Demonstrated: Hyde Park on the normal map gives
  k=0 and identical output. Only centres outside ±180 change. A Fiji
  polygon straddling 180 can't be fixed by any single shift.
- **Getting agent-browser working in the container (dead ends and
  workarounds):**
  - `npm i -g agent-browser` failed with EACCES (`/usr/local` is
    root-owned). Fixed with `npm config set prefix ~/.npm-global`.
  - Chrome failed on a missing `libglib-2.0.so.0`.
    `agent-browser install --with-deps` needs root or sudo, which isn't
    available.
  - Workaround: `apt-get update` with
    `-o Dir::State::Lists=~/chromelibs/lists -o Dir::Cache=~/chromelibs/cache`,
    then `apt-get download` of 71 packages, `dpkg -x` into
    `~/chromelibs/root`, and `LD_LIBRARY_PATH` pointed there.
  - Chrome launched by agent-browser kept "relaunching" or failing with
    "CDP response channel closed", even though `agent-browser doctor`'s
    launch test passed. Setting `AGENT_BROWSER_ARGS` didn't visibly fix it;
    the cause was unconfirmed.
  - What worked: launch Chrome manually with `--headless=new --no-sandbox
    --disable-dev-shm-usage --remote-debugging-port=9333`, detached with
    `setsid nohup`, then run `agent-browser --cdp 9333`.
  - The Leaflet map instance was read without changing the app, by patching
    `L.Map.prototype.fire` from the Vite dev module
    (`/node_modules/.vite/deps/leaflet.js?v=<hash>`; the version-hashed URL
    is needed to get the same module instance).
  - The container has no `curl` or `ps`; used `python3 urllib` and
    `/proc/*/cmdline` instead. An over-broad `/proc` match killed unrelated
    wrapper shells once (Vite and Chrome survived).
- **Local dev without a PostHog token:** `getDistinctId()` returns null, so
  `/api/query` returns 422 and the UI shows a blank message. The stub
  defaults `distinctId` locally to work around this.

## Decisions and trade-offs

- **Decision:** fix with A2 (lock the map to a single world) rather than B
  (frontend polygon shift) or C (backend normalisation).
  **Why:** removes the cause with a few lines of configuration and covers
  every navigation route (VF-5 in the spec). C would leave result markers
  off screen on a world copy and hide the regression in the logs.
  **Trade-off:** hard map edge at the Pacific, grey margins at low zoom on
  wide screens, and antimeridian polygons (e.g. Fiji) can't be drawn
  (documented limitation).
- **Decision:** don't rely on `worldCopyJump` (A1).
  **Why:** it only wraps on drag and keyboard pans, not zoom, so it makes
  the bug rarer rather than fixing it.
- **Decision:** the backend rejects out-of-range coordinates (422) rather
  than wrapping them.
  **Why:** any future frontend regression shows up as a logged client
  error, instead of a 500 or a silent repair.
- **Decision:** D includes a catch-all 500 JSON handler, and E applies to
  both `/api/query` and `/api/narrate`.
  **Why:** the user-facing problem was the silent hang on any failure, not
  only this bug.
  **Trade-off:** slightly wider scope than the bug itself; narration was
  included as a judgement call and flagged for review.
- **Decision:** proposed agent-container fix (untested): bake Chrome's
  libraries and fonts in as root, pin `node:22-bookworm-slim`, set
  `AGENT_BROWSER_ARGS="--no-sandbox,--disable-dev-shm-usage"`, and add
  `shm_size: "1gb"` and `init: true` in compose.
  **Trade-off:** `--no-sandbox` relies on the container as the isolation
  boundary.

## Blockers

None blocking. Open items:
- Spec REQ-004: `MIN_ZOOM` should stay 2 or be raised to 3. Decide from
  agent-browser screenshots at 1280px and 1920px during implementation.
- The agent-container changes are unverified until the image is rebuilt.
  `agent-browser doctor` passing isn't sufficient: the real check is
  `open` plus `snapshot -i` on a page.

## Next steps

1. Implement the spec test-first, in order: E (`src/lib/http.ts`
   `readJsonSafely`, `MapView`, `QueryPanel`, `NarrationControl`), then D
   (`models/query.py` range checks, new `services/error_handlers.py`,
   registration in `main.create_app`), then A2 (`WORLD_BOUNDS`,
   `maxBounds`, `maxBoundsViscosity`, `noWrap` in `MapView.tsx`), then the
   `ARCHITECTURE.md` update.
2. Re-run the Toowoomba agent-browser repro after A2. Validation steps are
   in spec §12.
3. Apply and verify the agent-container changes
   (`agent_container_updates_201126.md`). Push all work before
   `docker compose up -d --build`, because recreating the container wipes
   its filesystem.
4. After deploy: confirm the Toowoomba flow on the live app, and check that
   no further `pygeohash` `ValueError` appears in Cloud Run logs.
5. Separately: make local dev work without a PostHog token (`distinctId`
   null → 422).
6. Carried over from the 06 Nov work summary: the semantic-search track
   items, unaffected by this session.
