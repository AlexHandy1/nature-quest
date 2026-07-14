---
name: summarise-session
description: This skill should be used when the user runs "/summarise-session", asks to "summarise the session", "write up what we did", "save session notes", "update the work summary", or wants a record of the current session's progress saved to a markdown file.
version: 0.1.0
---

# Summarise Session

Review the current conversation and write a structured session summary to the appropriate `WORK_SUMMARY_DDMMYY.md` file.

## Step 1: Locate or create the docs directory

Look for a `planning_and_status_docs/` directory in the working directory:

```bash
find . -maxdepth 2 -type d -name "planning_and_status_docs"
```

If it does not exist, create it:

```bash
mkdir -p planning_and_status_docs
```

## Step 2: Determine today's filename

Today's date is available via `currentDate` in context. Convert it to DDMMYY format for the filename, e.g. `WORK_SUMMARY_060626.md`.

## Step 3: Decide whether to create or update

- **File does not exist** → create it fresh with the full structure below.
- **File exists** → read it, then decide:
  - If today's session is clearly a continuation of the same work (same features, same scripts) → update the relevant sections in place.
  - If today's session is a distinct new piece of work → append a new dated section (`## Session 2 — HH:MM` or similar) rather than overwriting.

## Step 4: Write the summary

Use this structure. Be specific and concrete — quote actual filenames, commands, decisions, and error messages where relevant. Do not pad.

```markdown
# Work Summary — DD Month YYYY

## What was built
Concrete deliverables: files created or changed, scripts that now work, DB updates made.
One or two sentences per item. Name the files.

## What was explored / learnt
Approaches tried, tools evaluated, debugging discoveries — including dead ends.
Dead ends are worth recording so they are not repeated.

## Decisions and trade-offs
Explicit choices made and the reasoning behind them.
Format: **Decision:** ... **Why:** ... **Trade-off:** ...

## Blockers
Anything unresolved that is blocking progress. Be specific about the symptom and what was tried.
If nothing is blocked, omit this section.

## Next steps
Prioritised list drawn from the session, ready to pick up next time.
```

## Guidelines

- Draw entirely from the current conversation — do not invent or infer things that were not discussed.
- Preserve exact file paths, command flags, error messages, and URLs — these make the doc useful for future sessions.
- If the file already exists and you are updating it, preserve any content from prior sessions unless it is directly superseded by today's work.
- Keep the tone factual and terse. This is a reference document, not a narrative.
