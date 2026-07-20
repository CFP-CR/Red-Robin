# Operator smoke — AI assistant Task 2 #23 (reactive grounded recommendation)

The live surface (the `#advisor-dock` render over a real CS beacon + the
enricher/reducer over real output) is NOT CI-testable — this manual smoke is its
only acceptance (skill **smoke-test**, `development-workflow.md` §5). Checkpoints
are tagged `[Task 2]` (must pass now) or `[later task]` (deliberately NOT
expected yet — do not fail the smoke on these).

## Preconditions
- Daemon built + running with `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` exported
  (without them the assistant/KB run degraded — see the degraded checkpoint).
- KB index populated (`redrobin kb add ...` / `kb ingest`) so `kb_retrieve` has
  content — otherwise references cite only the extracted facts (no KB tradecraft).
- The Textual TUI connected (`redrobin tui`), a live CS beacon on a lab box, and
  the CS adapter connected to the daemon.

## Setup
1. On the lab box, plant the fixture:
   `powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1`
2. Note the printed fixture path (`C:\Users\Public\RR-Smoke` or the Documents fallback).

## Run — step 1: loot the config
From the beacon:
```
shell dir  "C:\Users\Public\RR-Smoke"
shell type "C:\Users\Public\RR-Smoke\db_backup.config"
shell type "C:\Users\Public\RR-Smoke\notes_old.txt"
```

- **[Task 2] Checkpoint A — an advisor card appears** in `#advisor-dock` within a
  few seconds (the dock reveals itself; it was hidden before). It appears
  *reactively*, triggered by the command output — you did not ask for it.
- **[Task 2] Checkpoint B — the card has all 5 parts:** `→` a concrete WHAT
  (e.g. validate `svc_reports` against SQL01), `why` a tradecraft rationale,
  `opsec` INFO/WARN/DANGER with a reason, `conf` a confidence that names what it
  is NOT sure about, `refs` grounding.
- **[Task 2] Checkpoint C — references are grounded (extracted facts, not raw
  output):** `refs` cite the freshly-extracted facts the enricher pulled from your
  command (e.g. the `svc_reports` credential extracted from the
  `type db_backup.config` output) and/or a KB chunk (e.g.
  `vault/Techniques/...#chunk_n`) — grounded, not invented. Task 2 (#23) has NO
  Operational Journal (raw transcript) yet, so do NOT expect `refs` to quote the raw
  output text verbatim.
- **[Task 2] Checkpoint D — advisory only:** the card says advisory / nothing
  dispatched; NO command was sent to the beacon (read-only by construction).

## Run — step 2: follow the hint
```
shell dir  "C:\Users\Public\RR-Smoke\exports"
shell type "C:\Users\Public\RR-Smoke\exports\svc_sql_readme.txt"
```

- **[Task 2] Checkpoint E — a second recommendation** appears for the `svc_sql`
  finding, grounded in the new output (the assistant reacted again on new state).
- **[Task 2] Checkpoint F — signal-not-noise:** re-running the SAME `type`
  (no new state) does NOT produce a duplicate card (dedup/novelty/cooldown).

## Degraded / never-crash
- **[Task 2] Checkpoint G:** restart the daemon with `ANTHROPIC_API_KEY` unset →
  the daemon still boots, the TUI still works, NO advisor cards appear (silent),
  nothing crashes.

## Explicitly out of scope (do NOT expect — later tasks)
- **[later task]** A *proactive* card on a lull / phase boundary with no command.
- **[later task]** Asking the assistant a question in a Chat pane ("what's my
  path to DA?") and getting a deep answer (conversational mode).
- **[later task]** Pushing back ("too loud") and it capturing a lesson (learning loop).
- **[later task]** The assistant writing `current_phase` / objective status, or a
  propose-and-confirm affordance (interpretation-write).
- **[later task]** `search_journal` / `get_lessons` tools, or the deep Sonnet sub-agent.

## Teardown
`powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup`
