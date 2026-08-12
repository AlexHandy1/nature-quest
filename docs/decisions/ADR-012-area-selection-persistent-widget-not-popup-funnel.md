# ADR-012: Area Selection as a Persistent Widget, Not a Popup/Funnel

## Status
Accepted

## Date
2026-08-12

## Context

`docs/specs/spec-tool-draw-your-own-area-100826.md` (Slice 9) specified a state machine for choosing between the fixed Retiro Park area and a user-drawn custom area:

- On load: a choice popup overlaid on the map (`AreaChoicePopup`), gating the query form until a choice was made.
- Selecting "Draw your own area" opened a draw toolbar; a custom "Confirm Area" button (backed by a `DrawConfirmBar` component) validated and committed the drawn shape.
- Once a result was showing, a docked bar exposed "Redraw area" / "Switch to Retiro" actions — but only after a *resolved* result, and only reachable from that specific state.

This was built essentially as specified, then manually tested against the real app. Manual testing surfaced several real, user-blocking problems the spec's design didn't anticipate:

- The query form (a floating panel) visually and functionally blocked Leaflet-draw's own toolbar controls, making the draw tool partly unusable.
- Mode-switch actions (draw ↔ fixed) only existed inside the post-result docked bar, so a user who hadn't yet gotten a resolved result — or who switched to fixed mode — had no way back into the other mode without refreshing the page.
- The custom "Confirm Area" button duplicated functionality Leaflet-draw's own toolbar already provides (finishing a shape closes and creates it), and looked like a second, confusing "mirror" control next to Leaflet's native polygon/edit/delete icons.
- The drawn polygon disappeared from the map once its owning component unmounted (leaving the search area invisible after a query completed), because "current shape being edited" and "confirmed search area" were the same piece of state instead of two.

## Decision

1. **Area mode is a persistent, always-visible, always-changeable widget (`AreaControl`)**, not a one-time popup gate. It sits in a real navbar (`app-shell` → `nav-bar`, normal document flow above the map) alongside the query form and branding, rather than floating over the map. Mode can be changed from any state, not just after a resolved result.
2. **The confirmed polygon is separate state from the in-progress drawing.** `MapView` renders a persistent `<Polygon>` overlay driven directly from `areaState.polygon`, independent of whether the draw toolbar (`DrawAreaControl`) is currently mounted. Confirming a draw just updates `areaState`; nothing gets torn down.
3. **No custom "Confirm Area" button.** A drawn shape auto-confirms the moment Leaflet-draw's own `CREATED`/`EDITED` events fire and the shape passes validation (vertex count, area cap) — the same events that already close/finish the shape via Leaflet's native polygon tool. Leaflet's own edit (pencil) and delete (trash) controls are enabled and used as the redraw/fix-a-mistake mechanism, instead of a parallel custom control. `DrawConfirmBar` (the custom button) was deleted; the only remaining custom UI in draw mode is a passive `AreaSizeWarning` message shown when a drawn shape exceeds the area cap.
4. **The query form and area toggle live in one real navbar (`nav-bar`)**, not a floating overlay panel (`QueryPanel`'s old `position: absolute` card). This was also the fix for the toolbar-blocking problem: Leaflet's own controls anchor to the map container's own top-left corner, so once the surrounding UI stopped overlaying the map, the conflict disappeared structurally rather than needing per-element z-index/positioning fixes.

`AreaChoicePopup.tsx` and `DrawConfirmBar.tsx` were deleted outright (not left dormant), consistent with this project's established convention for components fully superseded by a redesign.

## Alternatives Considered

### Keep the popup/funnel, fix each symptom individually
- Pros: smaller diff per fix; spec stays accurate as originally written.
- Cons: each symptom (toolbar blocking, dead-end mode switches, disappearing polygon, duplicate confirm control) traced back to the same underlying design choice (mode-as-a-one-time-gate, confirmed-state-as-editing-state). Patching symptoms individually would have left the structural cause in place and likely surfaced more of the same class of bug later.
- Rejected because: the user explicitly asked to simplify the design rather than keep patching it, after several rounds of surface-level fixes didn't resolve the underlying issues.

### Keep the custom "Confirm Area" button, remove Leaflet's native edit/delete controls instead
- Pros: one clear, explicit "commit" action; matches the original prototype's UX pattern this slice was based on.
- Cons: the native Leaflet controls the user had already found clean and usable would have been removed instead of the redundant custom ones; still requires an extra click beyond finishing the shape, which Leaflet-draw already treats as "done."
- Rejected because: direct user feedback favored keeping Leaflet's native, already-working controls and removing the ones that were "big and clunky."

## Consequences

- The implementation now materially diverges from `spec-tool-draw-your-own-area-100826.md`'s original state-machine description (REQ-005 through REQ-009 as written describe the popup/docked-bar/confirm-button design, not what was actually built). The spec's own status line has been updated to point here; the spec document itself was not rewritten line-by-line to match the final implementation.
- `QueryPanel` lost its `docked`/floating-panel behavior and its resolved-outcome message display (a related, separate simplification made in the same pass — see `docs/status_docs/WORK_SUMMARY_120826.md`) — it is now a thin form embedded directly in `nav-bar`, with no independent visual state of its own beyond loading/non-resolved-outcome messages.
- Automated end-to-end coverage for the resulting draw-your-own-area flow was not achieved this session — see the "known gaps" comment block at the end of `tests/e2e_web_smoke_test.py` for the full, current list and why. Manual verification is the only current check on this flow's correctness.
