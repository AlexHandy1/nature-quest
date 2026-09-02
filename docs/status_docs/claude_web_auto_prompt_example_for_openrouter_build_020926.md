# Example Claude Code Web (Auto Mode) Prompt — OpenRouter Taxon Resolution Build

date: 2026-09-02

Context: drafted while planning how to kick off `docs/specs/spec-architecture-openrouter-taxon-resolution-280826.md`
using Claude Code on the web in autonomous/cloud mode. Captures the caveats worked through in that session:

- Cloud sessions run in an isolated VM with restricted network access by default — `openrouter.ai` and PostHog's
  ingestion host are not in the Trusted default allowlist, so the environment's network access level needs to be
  set to Custom with those domains added (or reachability confirmed some other way) before this will work at all.
- The environment's plain "Environment variables" box is `.env`-format and explicitly readable by anyone using
  that environment — not a secrets store. The safer "API credentials" mechanism (key held by Anthropic's agent
  proxy, attached to matching outbound requests, never readable inside the session) was checked for and was not
  available on this account/environment (only Allowed Domains / Environment variables / Setup script were present).
  Fell back to a scoped, spend-capped OpenRouter API key placed directly in the environment variable box, to be
  revoked after the session.
- `ANTHROPIC_API_KEY` is not needed — the spec's rollback-coverage test (REQ-015) is mocked, no real Anthropic call.
- `POSTHOG_PROJECT_TOKEN` is deliberately left unset — `test_ai_observability_capture_eval.py`'s live PostHog case
  is already guarded to skip cleanly without it (`REQUIRES_POSTHOG`), and the PostHog dashboard confirmation
  (AC-002) is an explicitly manual step in the spec anyway, done separately by a human afterward.
- The live smoke test of `POST /api/query` (called for in the spec's build steps and §12 Validation Criteria) was
  excluded from the agent's task — browser automation (`agent-browser`) isn't expected to work in this cloud
  sandbox. Deferred to be run locally, by hand, after the session finishes.
- The eval tier (`pytest -m eval`) is excluded by default (`pytest.ini`: `addopts = -m "not eval"`), so the prompt
  has to explicitly instruct the agent to run it — otherwise an autonomous agent has no reason to know the
  eval suites (the actual acceptance-criteria gate, REQ-013/AC-004) even exist as a separate opt-in step.
- **Lesson learned mid-build**: switching network access to Custom to add `openrouter.ai`/PostHog's host silently
  dropped the Trusted defaults (`pypi.org`, `files.pythonhosted.org`, etc.) — Custom mode *replaces* the default
  allowlist unless you also check "Also include default list of common package managers" in the environment
  editor. First attempt stalled and failed on `pip install openai==2.53.0` with a 403 from `pypi.org` as a result.
  Also confirmed the fix isn't hot-reloaded into an already-running session — the environment's network policy is
  baked in at VM provisioning time, so a stalled session must be abandoned (not retried) and a fresh session
  started against the corrected environment. The prompt below adds an early reachability check for exactly this
  reason: fail fast in seconds, not mid-setup.

## The prompt

```
Before doing anything else: run `curl -sI https://pypi.org` and `curl -sI https://openrouter.ai`
to confirm both are reachable from this environment. If either fails (403, timeout, DNS
failure, or any non-2xx/3xx), STOP immediately and tell me — don't retry more than once,
don't attempt to work around it (no --trusted-host, no vendored packages, no alternate
index). A network/config-level block needs to be fixed in the environment settings, not
worked around from inside the session.

Implement the spec at docs/specs/spec-architecture-openrouter-taxon-resolution-280826.md in full.
Read the whole spec first, plus ARCHITECTURE.md and any READMEs it points to, before writing code.

Follow this repo's CLAUDE.md workflow: TDD (red-green-refactor per /tdd and /testing),
commit after each coherent logical change with a short message, keep changes scoped to
this slice only (no unrelated refactors).

Build steps are listed at the top of the spec ("Build steps at a glance") — follow them in order.

Critical, non-optional step — do not skip or stop before this:
REQ-013 / AC-004 requires the LIVE eval suites to pass against the real OpenRouter path
before this is done. These are excluded by default (pytest.ini: `addopts = -m "not eval"`),
so you must run them explicitly:

  pytest -m eval app/backend/tests/evals/test_taxon_resolution_eval.py
  pytest -m eval app/backend/tests/evals/test_full_pipeline_eval.py

If any case fails — including "I want to see Turtles" → class/Testudines — iterate on
TAXON_GUIDANCE's wording in services/anthropic_client.py (a prompt edit only) and re-run
both suites. Do NOT change the seeded queries or expected values in either eval file to
make them pass. Keep iterating until both suites are fully green. This may take several
rounds — that's expected, keep going rather than stopping to ask.

OPENROUTER_API_KEY is already set as an environment variable in this cloud environment
(a scoped, spend-capped key made for this task) — don't print it, log it, or echo the
environment in full.

POSTHOG_PROJECT_TOKEN is deliberately NOT set in this environment. This is expected, not
a gap to fix: test_ai_observability_capture_eval.py's live PostHog case is already guarded
to skip cleanly when it's absent — let it skip. I'll do the real PostHog dashboard
confirmation (AC-002) myself separately, later.

Do NOT attempt a live smoke test of POST /api/query, and do not invoke the agent-browser
skill or any browser automation — this cloud environment doesn't support it and it will
just fail or stall. I'll run the smoke test myself locally after this session finishes.
Rely on the eval suites above (which do make real OpenRouter calls end-to-end through the
actual production code path) as your live-correctness signal instead.

Also required before you consider this done:
- Keep services/anthropic_client.py's resolve_taxon_filters intact and covered by at
  least one unit test using an Anthropic-shaped mock (REQ-015) — this is the rollback path.
- No model-specific logic anywhere outside the single MODEL constant in
  services/openrouter_taxon_client.py (REQ-014) — verify with a grep for the model string.
- ruff and mypy clean.

When everything above is green, stop and give me a summary: which eval iterations were
needed on TAXON_GUIDANCE (if any), final pass/fail state of both eval suites, what's left
undone (the smoke test and PostHog dashboard check, both deferred to me), and anything
that didn't go as the spec expected.
```

## Follow-up still required from a human after the session

- Run the live smoke test of `POST /api/query` locally (spec build step 8, §12 Validation Criteria).
- Manually confirm the `$ai_generation` PostHog event for this call site (AC-002).
- Revoke/rotate the scoped OpenRouter API key used for the session.
- Pull the branch locally and re-run `ruff`/`mypy` plus `pytest -m eval` once before merging, per this project's
  established CI-parity habit (`WORK_SUMMARY_210826.md`).
