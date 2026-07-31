# Operator smoke — AI assistant (Tasks 2/4/6 #23/#25/#27 + #28 + gate removal — the full assistant live surface)

The live surface (the assistant's advisories + confirms + answers in the Chat pane over a real CS beacon +
the enricher/reducer over real output) is NOT CI-testable — this manual smoke is its
only acceptance (skill **smoke-test**, `development-workflow.md` §5). Checkpoints are
tagged by the unit that must pass now (`[Task 2]`, `[Task 4]`, `[Task 6]`, `[#28]`,
`[gate removal]`) or `[later task]` (deliberately NOT expected yet — do not fail the
smoke on these).

> **Scope note (2026-07-26, pre-flight gate removal).** Red Robin is advisory-only: the
> `.cna` observes and ships commands + output to the daemon, and the assistant answers in
> the TUI Chat pane. Nothing is intercepted, nothing is blocked, and Red Robin prints
> **nothing** in the beacon console. There are no verdicts and no verdict counters — the
> only OPSEC signal is the assistant's `opsec` field on a recommendation
> (INFO/WARN/DANGER/ESCALATE). The `[gate removal]` checkpoints below are the assistant-side
> acceptance for that removal.

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
> **Wait ~30 s after step 1's advisory before running step 2** — the assistant cooldown
> (`assistant_cooldown_sec`, default 30.0) is signal-not-noise and will *correctly*
> suppress a second advisory fired too soon, so a fast step 2 with no Checkpoint-E advisory is
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
  the last advisory, the novelty gate correctly stays silent — advance state first.)
- **[Task 4] Checkpoint I — Material change:** run a command whose output reveals a
  new defence (e.g. an AV/EDR banner). EXPECT: either the reactive advisory (if reactive
  spoke) or, if reactive stayed silent, a proactive watch-your-back advisory about the
  new defence — never both for the same state.
- **[Task 4] Checkpoint J — Rate-limit / signal-not-noise:** after a proactive advisory,
  confirm no second proactive advisory within `N` even across further idle. Advisory-only
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
loot steps first, so the assistant has a picture to answer over). The Chat pane occupies
the CENTER of the TUI — press `c` to focus it, type in the Input, Enter to send.
Assistant-authored lines render as `Red Robin  …`; the topbar carries the engagement id +
connection dot and nothing else (verdict counters went with the pre-flight gate — see
Checkpoint V). Batch these onto the same live run as the Task 2/4/6 checkpoints.

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

## [gate removal] Advisory-only — the pre-flight gate is gone (2026-07-26)

Assistant-side acceptance for the pre-flight gate's removal: the adapter captures, the core
advises, and the capture → enricher → assistant path is intact. Prereq: the Task 2
preconditions (daemon + keys + TUI connected), a live CS beacon with `redrobin.cna` loaded,
and the fixture planted (Setup above). Batch these onto the same live run.
(The adapter-side acceptance — no interception, no dead routes —
is `adapters/cobaltstrike/tests/test_cna_smoke.md` §1; do not duplicate it here.)

- **[gate removal] Checkpoint T — a formerly-DANGER command just runs.** In the beacon
  console, run the command the gate used to flag DANGER:
  ```
  mimikatz sekurlsa::logonpasswords
  ```
  EXPECT: it dispatches **immediately** — CS's own `Tasked beacon to run mimikatz ...`,
  then real output. NO interception, NO prompt, no perceptible delay before the task is
  sent, and **NO `[Red Robin]` line of any kind in the beacon console** — no `DANGER:`, no
  `WARN:`, no `gate UNAVAILABLE`, no `Layer B`. Red Robin no longer speaks in the beacon
  console at all; it speaks in the TUI Chat pane.
- **[gate removal] Checkpoint U — capture → enricher → assistant survived.** Run a command
  that produces real output, against the fixture:
  ```
  shell type "C:\Users\Public\RR-Smoke\db_backup.config"
  ```
  EXPECT, in the TUI (not the beacon console): (a) an advisory appears in the Chat pane
  labeled `Red Robin · advisory`, carrying all 5 parts (as Checkpoint B); and (b) the
  Working-Memory panel's counters advance on the `hosts N   creds N   findings N` line —
  the `svc_reports` / SQL01 credential from this file lands in `creds` and the enricher's
  extraction lands in `findings`. Both halves must pass: (a) proves the assistant still
  reacts, (b) proves the enricher still fills working memory from captured output.
- **[gate removal] Checkpoint V — the topbar has no verdict counters.** Read the TUI
  topbar. EXPECT exactly `Red Robin · <engagement-slug> · ● connected` — engagement id and
  connection state (green dot) and nothing else. There is NO verdict count, no
  PASS/WARN/BLOCK/ESCALATE tally, and no Verdicts pane anywhere in the layout (the center
  pane is Chat).
- **[gate removal] Checkpoint W — the command trail persists.** After several commands,
  inspect the engagement's episodic store:
  ```bash
  uv run --no-sync python tools/inspect_episodic.py ~/.redrobin/engagements/<slug>/episodic.db
  ```
  EXPECT: one `episodes` row per command you ran, no duplicates, and the real command verb
  (not `?`). The table must have NO `verdict` / `reason` columns (they are dropped on open
  from a legacy DB). Then leave the daemon running: once `REDROBIN_L5_WINDOW` episodes
  (default 25) have accumulated, the summariser loop (every `REDROBIN_L5_INTERVAL_SEC`,
  default 30 s) writes an L5 summary. Confirm one appears:
  ```bash
  uv run --no-sync python tools/inspect_episodic.py ~/.redrobin/context_summaries.db
  ```
  EXPECT: at least one `context_summaries` row for this engagement. (L5 summaries live in
  the root-level `context_summaries.db`, NOT in `episodic.db` — they are cross-engagement
  state. If you ran fewer than `REDROBIN_L5_WINDOW` commands, zero rows is correct, not a
  failure — run more, or lower `REDROBIN_L5_WINDOW` for the test.)

## Explicitly out of scope (do NOT expect — later tasks)
> The conversational Q&A + learning loop and the confirm-affordance visual are now DONE and
> live (Task-5 backend + #28 UI) — see the `[#28]` checkpoints above. They are no longer
> deferred; the only remaining out-of-scope items are the deep-agent tools below.
- **[later task]** `search_journal` / `get_lessons` tools, or the deep Sonnet sub-agent.

## Teardown
`powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup`
