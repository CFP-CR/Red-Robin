# Operator smoke — AI assistant (Tasks 2/4/6 #23/#25/#27 + #28 — the full assistant live surface)

The live surface (the assistant's advisories + confirms + answers in the Chat pane over a real CS beacon +
the enricher/reducer over real output) is NOT CI-testable — this manual smoke is its
only acceptance (skill **smoke-test**, `development-workflow.md` §5). Checkpoints are
tagged by the unit that must pass now (`[Task 2]`, `[Task 4]`, `[Task 6]`, `[#28]`) or
`[later task]` (deliberately NOT expected yet — do not fail the smoke on these).

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

- **[Task 2] Checkpoint A — an advisory appears** in the Chat pane within a few
  seconds, labeled `Red Robin · advisory`. It appears
  *reactively*, triggered by the command output — you did not ask for it.
- **[Task 2] Checkpoint B — the advisory has all 5 parts:** `→` a concrete WHAT
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
- **[Task 2] Checkpoint D — advisory only:** the advisory line says nothing
  dispatched; NO command was sent to the beacon (read-only by construction).

## Run — step 2: follow the hint
> **Wait ~30 s after step 1's card before running step 2** — the assistant cooldown
> (`assistant_cooldown_sec`, default 30.0) is signal-not-noise and will *correctly*
> suppress a second card fired too soon, so a fast step 2 with no Checkpoint-E card is
> the cooldown working, not a failure.

```
shell dir  "C:\Users\Public\RR-Smoke\exports"
shell type "C:\Users\Public\RR-Smoke\exports\svc_sql_readme.txt"
```

- **[Task 2] Checkpoint E — a second recommendation** appears for the `svc_sql`
  finding, grounded in the new output (the assistant reacted again on new state).
- **[Task 2] Checkpoint F — signal-not-noise:** re-running the SAME `type`
  (no new state) does NOT produce a duplicate advisory (dedup/novelty/cooldown).

## Degraded / never-crash
- **[Task 2] Checkpoint G:** restart the daemon with `ANTHROPIC_API_KEY` unset →
  the daemon still boots, the TUI still works, NO advisories appear in the Chat pane (silent),
  nothing crashes.

## [Task 4 (#25)] Proactive mode

Prereq: assistant enabled (`REDROBIN_ASSISTANT_ENABLED=1`) with an ANTHROPIC key;
for the test set a short lull + rate-limit so the cadence is observable:
`REDROBIN_ASSISTANT_LULL_SEC=60`, `REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC=60`.

- **[Task 4] Checkpoint H — Lull:** run one benign command so the assistant has
  state, then leave the beacon idle (no new commands) for > the lull. EXPECT: a
  *proactive* advisory appears in the Chat pane, labeled `Red Robin · heads-up`, with NO
  command having been run, carrying all 5 parts. (If the state is unchanged since
  the last card, the novelty gate correctly stays silent — advance state first.)
- **[Task 4] Checkpoint I — Material change:** run a command whose output reveals a
  new defence (e.g. an AV/EDR banner). EXPECT: either the reactive card (if reactive
  spoke) or, if reactive stayed silent, a proactive watch-your-back card about the
  new defence — never both for the same state.
- **[Task 4] Checkpoint J — Rate-limit / signal-not-noise:** after a proactive card,
  confirm no second proactive card within `N` even across further idle. Advisory-only
  throughout (nothing dispatched to the beacon).

## [Task 6 (#27)] Interpretation-write

Prereq: assistant enabled with an ANTHROPIC key (Task 2 preconditions). The assistant
now READS the shared picture as it works: on each command it may auto-write a soft,
reversible read (a phase nudge / progress bump) and, on a close/commit, PROPOSE a binary
confirm the operator resolves later — the beacon is never held. Batch these onto the same
live run as the Task 2/4 checkpoints.

- **[Task 6] Checkpoint K — Soft auto-write (the assistant's read):** run a couple of
  commands that advance an objective (e.g. recon → a first foothold on a host in an
  objective's scope). EXPECT: the Working-Memory panel's `current_phase` and/or the
  objective's `progress` update on their own — the assistant's inference, no operator
  edit. It is a SOFT read: reversible by a later operator edit/commit (the "labeled as
  the assistant's read" *presentation* is #28; the value + reversibility hold now).
- **[Task 6] Checkpoint L — Propose-and-confirm (close/commit):** advance state to where
  an objective looks DONE (or a kill-chain goal is reached). EXPECT: a pending confirm —
  a one-line yes/no question surfaced over `operator.question` (rendered by #28's Chat
  pane; until then observe it on the bus / logs as `assistant: interpretation proposed`).
  The beacon is NOT blocked while it is outstanding — you can keep tasking.
- **[Task 6] Checkpoint M — Resolve non-blocking (yes / no):** answer the pending confirm.
  On **yes**, EXPECT the objective commits (status → COMPLETE in the WM panel); on **no**,
  EXPECT the pending is dropped and nothing commits. Either way the resolution happens in a
  later cycle from the persisted WM — a plain operator question (no `question_id` matching a
  pending) NEVER commits, and the assistant never loops on its own proposal.
- **[Task 6] Checkpoint N — Signal-not-noise / no re-ask:** with state unchanged, confirm
  the interpretation does NOT re-fire (novelty-gated) and an already-pending confirm is NOT
  re-asked every command. Advisory + single-writer throughout (the assistant publishes
  `engagement.milestone`; the reducer writes WM — nothing dispatched to the beacon).

## [#28] Conversational + confirm affordance

Prereq: assistant enabled (`REDROBIN_ASSISTANT_ENABLED=1`) with an ANTHROPIC key, and
a live beacon that has already produced some engagement state (run at least the Task 2
loot steps first, so the assistant has a picture to answer over). The Chat pane REPLACES
the old Verdicts/Events pane in the CENTER of the TUI — press `c` to focus it, type in
the Input, Enter to send. Assistant-authored lines render as `Red Robin  …`; the topbar
verdict COUNT is unchanged. Batch these onto the same live run as the Task 2/4/6
checkpoints.

- **[#28] Checkpoint O — Ask (conversational Q&A):** type a question in the Chat pane
  (e.g. `what's my path to DA?`). EXPECT: your line echoes as the operator's, then a
  grounded `Red Robin  …` answer renders with `refs` (extracted facts / KB chunks) —
  grounded, not invented. This is the bidirectional round-trip: your message reached the
  assistant (published `operator.question`) and its `operator.answer` rendered back.
- **[#28] Checkpoint P — Correct (the learning loop):** type a correction / push-back
  (e.g. `that's too loud`). EXPECT: the assistant acknowledges and a LESSON is captured —
  confirm via the DB (the lessons table) OR by watching a later cycle honour it (a
  subsequent recommendation is quieter / cites the lesson). This is the Task-5 learning
  loop, now operator-usable straight from the chat.
- **[#28] Checkpoint Q — Confirm (interpretation-write, Task 6) resolves INLINE:** drive
  state to a close/commit (advance an objective to DONE, as in Checkpoint L). EXPECT: a
  pending `CONFIRM …  [reply /y or /n]` renders INLINE in the Chat pane (not merely on the
  bus / logs as in Task 6). Reply `/y` → the objective commits (status → COMPLETE in the
  Working-Memory panel); reply `/n` → the pending is dropped and nothing commits. The
  beacon is NEVER held while the confirm is outstanding — you can keep tasking throughout.
- **[#28] Checkpoint R — Safety (a plain question never commits):** with a confirm still
  pending, type an ordinary question — even a short one like `ok` or `status?`. EXPECT: it
  is treated as a QUESTION (a `Red Robin  …` answer), and it does NOT resolve or commit the
  pending confirm. ONLY the explicit `/y` / `/n` token resolves a confirm (the
  false-commit-proof routing; a minted ask id `q<N>` never matches a semantic
  `milestone_id`, so an ordinary word can't false-match a pending).
- **[#28] Checkpoint S — Never-crash (bad frame, either direction):** the chat socket
  survives a malformed frame — a bad/partial WS frame is dropped and the daemon receive
  loop / TUI worker keep running (the chat still sends + renders afterward). You should
  never be able to kill the socket from the chat.

## Explicitly out of scope (do NOT expect — later tasks)
> The conversational Q&A + learning loop and the confirm-affordance visual are now DONE and
> live (Task-5 backend + #28 UI) — see the `[#28]` checkpoints above. They are no longer
> deferred; the only remaining out-of-scope items are the deep-agent tools below.
- **[later task]** `search_journal` / `get_lessons` tools, or the deep Sonnet sub-agent.

## Teardown
`powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup`
