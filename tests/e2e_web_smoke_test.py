#!/usr/bin/env python3
"""
Deterministic end-to-end smoke test for the map query flow.

Manually run only - not part of CI/CD. Drives a real Chromium browser via
the agent-browser CLI (https://github.com/agent-browser) and makes exact
assertions against the DOM and the real /api/query network response body -
no LLM judgment involved anywhere in this script.

Local mode (default): starts both dev servers itself (backend on :8000,
frontend on :5173, matching README.md's documented commands), runs the
checklist, then stops both servers on exit.

Live mode (--url): points at an already-running deployment instead: no
servers are started or stopped.

Requires: `agent-browser` installed and on PATH (`npm i -g agent-browser &&
agent-browser install`).

Usage:
  python tests/e2e_web_smoke_test.py
  python tests/e2e_web_smoke_test.py --url https://<cloud-run-url>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "app" / "backend"
FRONTEND_DIR = REPO_ROOT / "app" / "frontend"
LOCAL_URL = "http://localhost:5173"
BACKEND_HEALTH_URL = "http://localhost:8000/health"
SESSION = "nature-quest-smoke-test"
GBIF_UNAVAILABLE_RETRIES = 3

UNRESOLVED_MESSAGE = (
    "Sorry, we couldn't match that to a category we support yet — "
    "try something like 'birds' or 'plants'."
)

SUBMIT_BUTTON = ".nav-bar__query button[type=submit]"
QUERY_INPUT = "#query"
AREA_LABEL = ".area-control__label"

OUT_DIR = REPO_ROOT / "tests" / ".smoke_test_output" / datetime.now().strftime("%Y%m%d_%H%M%S")

HEADED = False  # set from --headed in main(); read by ab()

results: list[tuple[str, bool, str]] = []
log_lines: list[str] = []


def log(message: str = "") -> None:
    print(message)
    log_lines.append(message)


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    log(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


def note(message: str) -> None:
    log(f"  [NOTE] {message}")


def write_report(base_url: str, headed: bool) -> Path:
    report_path = OUT_DIR / "report.md"
    header = [
        "# Nature Quest — e2e smoke test report",
        "",
        f"- **Run at**: {datetime.now().isoformat(timespec='seconds')}",
        f"- **Target**: {base_url}",
        f"- **Mode**: {'headed' if headed else 'headless'}",
        "",
        "```",
    ]
    footer = ["```"]
    report_path.write_text("\n".join(header + log_lines + footer) + "\n")
    return report_path


# ---------- agent-browser CLI wrapper ----------


def reset_session() -> None:
    """Best-effort close of any stale daemon/session left over from a prior
    run under the same session name — avoids the "daemon already running" /
    "Resource temporarily unavailable" failures a leftover session causes
    partway through a run."""
    try:
        subprocess.run(
            ["agent-browser", "close", "--session", SESSION],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pass


def ab(*args: str, input_data: str | None = None, timeout: float = 30.0) -> str:
    cmd = ["agent-browser", "--session", SESSION]
    if HEADED:
        cmd.append("--headed")
    cmd.extend(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"agent-browser {' '.join(args)} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise RuntimeError(f"agent-browser {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def ab_json(*args: str) -> dict:
    payload = json.loads(ab(*args, "--json"))
    if not payload.get("success"):
        raise RuntimeError(f"agent-browser {' '.join(args)} returned error: {payload.get('error')}")
    return payload["data"]


def get_count(selector: str) -> int:
    return int(ab("get", "count", selector))


def get_text(selector: str) -> str:
    return ab("get", "text", selector)


def get_box(selector: str) -> dict:
    return ab_json("get", "box", selector)


# ---------- local dev server management ----------


class LocalServers:
    def __init__(self) -> None:
        self.backend: subprocess.Popen | None = None
        self.frontend: subprocess.Popen | None = None

    def start(self) -> None:
        log("Starting backend (uvicorn) ...")
        uvicorn_bin = BACKEND_DIR / "venv" / "bin" / "uvicorn"
        if not uvicorn_bin.exists():
            raise RuntimeError(f"{uvicorn_bin} not found — set up the backend venv first (see README.md)")
        self.backend = subprocess.Popen(
            [str(uvicorn_bin), "main:app", "--port", "8000"],
            cwd=BACKEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_for(BACKEND_HEALTH_URL, "backend")

        log("Starting frontend (vite) ...")
        self.frontend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_for(LOCAL_URL, "frontend")

    @staticmethod
    def _wait_for(url: str, label: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(url, timeout=1)
                log(f"  {label} is up ({url})")
                return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        raise RuntimeError(f"{label} did not become reachable at {url} within {timeout}s")

    def stop(self) -> None:
        for proc, label in [(self.frontend, "frontend"), (self.backend, "backend")]:
            if proc is not None and proc.poll() is None:
                log(f"Stopping {label} ...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


# ---------- query submission ----------


def submit_query(query: str, check_loading: bool = True) -> dict:
    """Fills the query input, submits, waits for completion, and returns
    {elapsed_s, body} where body is the real /api/query JSON response."""
    ab("network", "requests", "--clear")
    ab("fill", QUERY_INPUT, query)

    start = time.monotonic()
    ab("click", SUBMIT_BUTTON)

    if check_loading:
        try:
            ab("wait", "--text", "Generating your walk")
            record(f'loading state shown for "{query}"', True)
        except RuntimeError as exc:
            record(f'loading state shown for "{query}"', False, str(exc))

    ab(
        "wait",
        "--fn",
        f"document.querySelector('{SUBMIT_BUTTON}').textContent.trim() === 'Create walk'",
    )
    elapsed = time.monotonic() - start

    requests = ab_json("network", "requests", "--filter", "/api/query", "--method", "POST")["requests"]
    if not requests:
        raise RuntimeError(f'No /api/query request captured for "{query}"')
    request_id = requests[-1]["requestId"]
    detail = ab_json("network", "request", request_id)
    body = json.loads(detail["responseBody"])

    return {"elapsed_s": elapsed, "body": body}


def submit_query_with_retry(query: str, max_attempts: int = GBIF_UNAVAILABLE_RETRIES) -> dict:
    """GBIF's live API is occasionally flaky (transient 502s) independent of
    this app's own code — retries here paper over that known external
    flakiness so the smoke test isn't a false alarm on a GBIF hiccup, while
    still surfacing it as a note rather than silently hiding it."""
    result = None
    for attempt in range(1, max_attempts + 1):
        result = submit_query(query, check_loading=(attempt == 1))
        if result["body"].get("status") != "gbif_unavailable":
            if attempt > 1:
                note(
                    f'"{query}" succeeded on attempt {attempt}/{max_attempts} after '
                    f"gbif_unavailable — known GBIF flakiness, not a code regression."
                )
            return result
        note(f'"{query}" attempt {attempt}/{max_attempts}: gbif_unavailable (502)')
    note(f'"{query}" hit gbif_unavailable on all {max_attempts} attempts — treating as a real failure')
    return result


def get_results_panel_species() -> list[dict]:
    count = get_count(".results-panel li")
    species = []
    for i in range(1, count + 1):
        name = get_text(f".results-panel li:nth-child({i}) .results-panel__name")
        obs_count = get_text(f".results-panel li:nth-child({i}) .results-panel__count")
        species.append({"name": name, "count_text": obs_count})
    return species


def check_result(query: str, result: dict, expected_species_count: int = 5) -> None:
    """Strict check for areas known to have real GBIF data (the fixed Retiro
    polygon) — expects a resolved outcome with a specific species count."""
    body = result["body"]
    elapsed = result["elapsed_s"]
    log(f'  elapsed: {elapsed:.1f}s')

    record(f'"{query}" resolved successfully', body.get("status") == "resolved", str(body.get("status")))

    species = body.get("species") or []
    record(
        f'"{query}" returned {expected_species_count} species',
        len(species) == expected_species_count,
        f"got {len(species)}",
    )

    marker_count = get_count(".map-marker-number")
    record(
        f'"{query}" marker count matches species count',
        marker_count == len(species),
        f"{marker_count} markers vs {len(species)} species",
    )

    route_line_count = get_count(".leaflet-overlay-pane path")
    record(f'"{query}" waypoints are connected by a route line', route_line_count >= 1)

    panel_species = get_results_panel_species()
    record(
        f'"{query}" results panel lists {expected_species_count} species',
        len(panel_species) == expected_species_count,
        f"got {len(panel_species)}",
    )
    all_named = all(s["name"].strip() for s in panel_species)
    record(f'"{query}" all results panel entries have a scientific name', all_named)

    screenshot_path = OUT_DIR / f"{query.replace(' ', '_')}.png"
    ab("screenshot", str(screenshot_path))
    log(f"  screenshot: {screenshot_path}")


def check_taxon_filters_contain(query: str, body: dict, expected_values: list[str]) -> None:
    taxon_values = {f.get("taxonValue") for f in (body.get("taxonFilters") or [])}
    for expected in expected_values:
        record(
            f'"{query}" taxonFilters include {expected}',
            expected in taxon_values,
            f"taxonFilters={sorted(taxon_values)}",
        )


def check_enrichment(query: str, body: dict) -> None:
    species = body.get("species") or []
    if not species:
        record(f'"{query}" enrichment check skipped', False, "no species in response")
        return

    all_have_common_name = all((s.get("common_name") or "").strip() for s in species)
    record(
        f'"{query}" every species has a common_name',
        all_have_common_name,
        str([s.get("common_name") for s in species]),
    )

    all_have_image = all((s.get("image_url") or "").startswith("http") for s in species)
    record(
        f'"{query}" every species has an image_url',
        all_have_image,
        str([s.get("image_url") for s in species]),
    )

    all_have_key = all(s.get("species_key") is not None for s in species)
    record(
        f'"{query}" every species has a species_key',
        all_have_key,
        str([s.get("species_key") for s in species]),
    )

    ab("click", ".results-panel li:nth-child(1) .results-panel__row")
    ab("wait", "--fn", "!!document.querySelector('.results-panel__detail')")
    expected_href = f"https://www.gbif.org/species/{species[0].get('species_key')}"
    link_count = get_count(f".results-panel__detail a[href='{expected_href}']")
    record(f'"{query}" first result links to the correct GBIF species page', link_count == 1, expected_href)
    image_count = get_count(".results-panel__detail img")
    record(f'"{query}" first result shows an image when expanded', image_count == 1)


def area_label_text() -> str:
    return get_text(AREA_LABEL)


# ---------- checklist ----------


def run_checklist(base_url: str) -> None:
    reset_session()
    log("\n== Landing page (default Retiro Park area) ==")
    ab("open", base_url)
    ab("wait", "--load", "networkidle")
    record("map container present", get_count(".leaflet-container") == 1)
    record("query input present", get_count(QUERY_INPUT) == 1)
    record("Nature Quest heading present", get_count(".nav-bar__wordmark") == 1)
    record("area control defaults to Retiro Park", "Retiro Park" in area_label_text(), area_label_text())
    ab("screenshot", str(OUT_DIR / "01_landing.png"))
    log(f"  screenshot: {OUT_DIR / '01_landing.png'}")

    log('\n== Query: "Show me some plants" (default Retiro Park area) ==')
    result = submit_query("Show me some plants")
    check_result("Show me some plants", result)
    check_taxon_filters_contain("Show me some plants", result["body"], ["Plantae"])
    kingdoms = {s.get("kingdom") for s in result["body"].get("species", [])}
    record('"Show me some plants" all species are kingdom Plantae', kingdoms == {"Plantae"}, str(kingdoms))

    log('\n== Query: "Show me some birds" (default Retiro Park area) ==')
    result = submit_query("Show me some birds")
    check_result("Show me some birds", result)
    check_taxon_filters_contain("Show me some birds", result["body"], ["Aves"])
    check_enrichment("Show me some birds", result["body"])

    log("\n== Mobile viewport ==")
    ab("set", "viewport", "390", "844")
    viewport_w, viewport_h = 390, 844

    scroll_width = int(ab("eval", "document.documentElement.scrollWidth"))
    record("mobile: no horizontal page overflow", scroll_width <= viewport_w, f"scrollWidth={scroll_width}")

    map_box = get_box(".leaflet-container")
    record("mobile: map retains a usable height", map_box["height"] >= 200, str(map_box))

    results_box = get_box(".results-panel")
    within_bounds = (
        results_box["x"] >= 0
        and results_box["y"] >= 0
        and results_box["x"] + results_box["width"] <= viewport_w
        and results_box["y"] + results_box["height"] <= viewport_h
    )
    record("mobile: results panel fully within viewport", within_bounds, str(results_box))

    ab("screenshot", str(OUT_DIR / "03_birds_mobile.png"))
    log(f"  screenshot: {OUT_DIR / '03_birds_mobile.png'}")
    ab("set", "viewport", "1280", "800")

    log('\n== Query: "Show me some fish and insects" (multi-taxon, GBIF-retry) ==')
    result = submit_query_with_retry("Show me some fish and insects")
    if result["body"].get("status") == "resolved":
        check_result("Show me some fish and insects", result)
        check_taxon_filters_contain(
            "Show me some fish and insects", result["body"], ["Insecta"]
        )
    else:
        record(
            "Show me some fish and insects resolved (after retries)",
            False,
            str(result["body"].get("status")),
        )

    log('\n== Query: "Surprise me" (unresolved) ==')
    result = submit_query("Surprise me")
    body = result["body"]
    record("unresolved message matches exactly", body.get("message") == UNRESOLVED_MESSAGE, body.get("message"))
    record("no markers shown for unresolved outcome", get_count(".map-marker-number") == 0)
    record("no results panel shown for unresolved outcome", get_count(".results-panel") == 0)

    ab("close")


def print_summary() -> int:
    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    failed = [r for r in results if not r[1]]
    for name, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        log(f"  [{mark}] {name}")
    log(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        log(f"\n{len(failed)} FAILURE(S):")
        for name, _, detail in failed:
            log(f"  - {name}" + (f" ({detail})" if detail else ""))
    log(f"\nScreenshots: {OUT_DIR}")
    return 1 if failed else 0


def main() -> int:
    global HEADED

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Live URL to test instead of starting local dev servers")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window while it runs (default: headless)",
    )
    args = parser.parse_args()
    HEADED = args.headed
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    servers = None
    base_url = args.url.rstrip("/") if args.url else LOCAL_URL
    try:
        if args.url:
            log(f"Live mode: {base_url}")
        else:
            servers = LocalServers()
            servers.start()

        run_checklist(base_url)
    finally:
        if servers is not None:
            servers.stop()

    exit_code = print_summary()
    report_path = write_report(base_url, HEADED)
    log(f"Report: {report_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

# ---------- known gaps: not covered by this script ----------
#
# The entire draw-your-own-area surface is untested here. It was attempted
# and removed (2026-08-12) after repeated flaky failures automating
# Leaflet-draw's polygon tool via synthetic agent-browser mouse events:
# "shape edges cannot cross" errors, dropped vertices, and inconsistent
# results even after several rounds of fixes (longer settle delays,
# verify-and-retry loops, geometry changes) — none of it reproduced
# reliably, despite the same interaction working fine when driven manually,
# by hand, in a real browser. The automation approach itself is the
# unsolved problem, not (as far as could be determined) the app's own
# drawing/validation logic. Concretely, none of the following is covered by
# any automated end-to-end test:
#   - Choosing "Draw your own area" and drawing a valid custom polygon.
#   - The drawn polygon auto-confirming (no separate "Confirm" button — the
#     app relies on Leaflet-draw's own CREATED event).
#   - The 25 km^2 area-too-large rejection and its inline warning message.
#   - Editing an already-drawn shape via Leaflet's native edit (pencil)
#     control (the EDITED-event auto-confirm/re-validate path).
#   - Deleting a drawn shape via Leaflet's native delete (trash) control.
#   - "Redraw area" replacing an already-confirmed custom polygon.
#   - Switching between "Explore Retiro Park" and a drawn custom area via
#     AreaControl, in either direction.
#   - Submitting a second, different query in the same drawn area without
#     redrawing.
#   - The "Use my location" geolocation flow (recenter + drawing there).
#   - Drawing/interacting with the map on a touch-sized mobile viewport.
#
# This is a real, non-trivial coverage gap for a slice whose whole point is
# the draw-your-own-area flow — manual verification is currently the only
# check this functionality gets. Revisiting the automation approach (a
# different agent-browser interaction pattern, or driving the app's own
# state directly rather than simulating map clicks) is a follow-up, not
# solved here.
#
# Also never covered by this script:
#   - The consent banner's Accept/Reject buttons.
#   - Rate-limit (10/minute) and daily-budget guardrail responses.
#   - Server-side polygon validation (422 on a missing/undersized/oversized
#     polygon) via a direct API call — covered by backend unit/integration
#     tests instead; a UI-driven check would depend on the same drawing
#     automation that's unsolved above.
#   - A live eval run against a real non-Retiro reference polygon with an
#     asserted species/centroid check (spec's suggested Rascafría reference
#     area) — a separate backend eval test, not part of this script.
