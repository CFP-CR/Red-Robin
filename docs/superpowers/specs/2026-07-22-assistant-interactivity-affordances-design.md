# Assistant Interactivity Affordances — Design Spec

**Date:** 2026-07-22 · **Branch:** `feat-ai-assistant` · **Status:** draft (open decisions flagged)
**Follows:** the AI-DLC — brainstorming-phase spec; next `writing-plans` → `auditing-plans` → `subagent-driven-development` → operator smoke → finishing.

Groups three findings from the live smoke into one coherent theme — **make the assistant's interactive moments correct AND legible** (Claude-Code-style): (A) a close/commit reliably ASKS, (B) that ask is a prominent prompt, not a buried chat line, (C) the operator can see when the assistant is working.

---

## §1. Context & Problem (all three surfaced in the 2026-07-22 live smoke)
- **A — a close auto-committed without asking.** `parse_interpretation` (`parser.py:186`) sets `needs_confirm` from the MODEL (`bool(data.get("needs_confirm")) and bool(milestone_id)`); nothing forces it. The model marked obj-1 COMPLETE with `needs_confirm=false` → `milestone_events` (interpret.py:34) auto-committed it → the operator was never asked and didn't think obj-1 was done. The propose-and-confirm invariant is prompt-enforced only, and the model is unreliable at it.
- **B — when it DOES ask, the ask isn't legible.** The confirm renders as one more line in the chat scrollback (`Red Robin · confirm … [/y /n]`); it scrolls away and doesn't read as a deliberate yes/no decision the way Claude Code's approval prompt does. (Confirmed: proven that a bare `y` can't false-commit — the routing is safe; the gap is purely the affordance.)
- **C — no "working" signal.** While a cycle runs (reactive/proactive/answer/interpret) the TUI shows nothing, so the assistant can look frozen. Operator asked for a Claude-Code-style "thinking" indicator.

## §2. Goal
One increment: (A) enforce confirm-on-close in CODE; (B) render a pending confirm as a **prominent, non-blocking** prompt (distinct from the chat log, pinned until resolved) + reflect it in the input; (C) show a transient **"thinking" spinner** driven by a new `assistant.thinking` bus signal, covering all cycles. No change to the reasoning, the false-commit routing, or the gate.

## §3. Pillars
- **Clarity (the driver).** The operator must (a) always be asked before an objective is closed, (b) see that ask unmistakably, (c) know when the assistant is working. Matches the "one brain, legible" intent.
- **Maintainability.** Reuse the existing seams: the frame-registry (`@REGISTRY.register`) for the new `assistant.thinking` handler (like `advisor.recommendation`); a reserved-then-revealed pane for the confirm bar (the pattern the old `#advisor-dock` used); enforce-by-construction in the parser (mirrors the #28 false-commit fix).
- **Scalability.** Bounded: the confirm bar shows the current pending(s); the spinner is one transient widget. No per-frame cost.

## §4. Design

### §4.A Confirm-on-close enforcement (application — `parser.py`)
- New module constant `_COMMIT_STATUSES = {"COMPLETE", "COMPLETED", "DONE", "ACHIEVED", "CLOSED", "RESOLVED"}` (upper-case set).
- In `parse_interpretation`, hoist `status` + `prompt`, then FORCE the confirm for a terminal status:
  ```python
  status = str(data.get("status") or "").strip()
  prompt = str(data.get("prompt") or "").strip()
  needs_confirm = bool(data.get("needs_confirm")) and bool(milestone_id)
  if milestone_id and status.upper() in _COMMIT_STATUSES:
      needs_confirm = True                       # a close/commit ALWAYS asks — enforce, don't trust the model
      if not prompt:
          prompt = f"Mark {milestone_id} {status}?"   # synthesize the question if the model omitted it
  return Interpretation(phase=phase, milestone_id=milestone_id, status=status,
                        progress_pct=progress, needs_confirm=needs_confirm, prompt=prompt)
  ```
- Soft writes (progress bumps, `in_progress`, phase nudges) are UNCHANGED (stay auto). Enforce-by-construction, mirroring the #28 false-commit hardening.

### §4.B Prominent, non-blocking confirm affordance (TUI)
- **Twist vs Claude Code:** CC's approval prompt BLOCKS the agent; Red Robin's confirm is **NON-BLOCKING by design** (the beacon is never held — the operator keeps tasking). So: prominent + pinned, but NOT modal.
- A reserved **confirm bar** (`#confirm-bar`, `display:none` until a confirm is pending — the `#advisor-dock` reveal pattern) sits **between the chat log and the input** inside the ChatPane. When ≥1 confirm is pending it reveals and renders, distinct from chat lines (accent border, a clear marker):
  ```
  ┌─ Red Robin needs your call ─────────────────────┐
  │ Mark obj-1 COMPLETE?                 /y yes  /n no│
  └──────────────────────────────────────────────────┘
  ```
- The **input placeholder** changes while a confirm is pending: `▶ Confirm pending — reply /y or /n` (restores to the normal placeholder when none pending).
- Drives off the SAME `_pending` list `ChatPane` already tracks (`_on_question` adds milestone ids; `on_input_submitted` consumes on `/y`/`/n`). Reveal/hide + placeholder update when `_pending` changes. The `/y /n` routing + false-commit safety are UNCHANGED.
- The confirm no longer renders as a chat-log line (it lives in the bar); the resolution outcome is already visible in the Working-Memory panel (status commit/drop). *(Open decision OD-1: also drop a one-line record into the chat log on resolve, or rely on the WM panel? Default: rely on WM panel + the bar clearing.)*

### §4.C "Thinking" indicator (assistant emit + TUI spinner)
- **New internal bus event `assistant.thinking`** (like `advisor.recommendation` — bus-only, forwarded to the TUI by `tui_ws`, NOT a `/v1/events` wire type): `{"type": "assistant.thinking", "payload": {"state": "start" | "stop"}}`.
- **Emit (assistant):** wrap each LLM-backed cycle so a `start` is published before the model call and a `stop` after (finally), covering reactive (`run_cycle`), proactive, conversational (`run_answer_cycle`), and interpretation (`run_interpretation_cycle`). Never-crash: emit failures are swallowed (a thinking signal must never break a cycle).
- **Render (TUI):** a transient status line in the ChatPane's bottom area — `✻ Red Robin is thinking…` (braille spinner, Rich `Spinner` in a `Static`, or a small `LoadingIndicator`). Best-practice timing (from research): **show only after ~200 ms** (avoid flash on fast cycles), **auto-erase** on `stop`, and **never delay input** (the spinner is display-only; typing is always responsive). A `stop` with no `start` (or a `start` already showing) is idempotent.

## §5. Resolved decisions
- **A: enforce in code** (not prompt-trusted) — the model auto-closed obj-1; enforce-by-construction is the #28-proven pattern.
- **B: pinned confirm bar + placeholder change, NON-blocking** — Claude-Code prominence adapted to RR's non-blocking design; a reserved-reveal pane (proven `#advisor-dock` pattern) keeps it from scrolling away.
- **C: `assistant.thinking` bus event + 200ms-delayed auto-erasing spinner** — a daemon signal covers ALL cycles (not just conversational); 200ms + never-delay-input per TUI best practice.

## §6. Open decisions (operator, before `writing-plans`)
- **OD-1 (B):** on resolve, also log a one-line outcome in the chat (`✓ obj-1 → COMPLETE` / `✗ declined`), or rely on the WM panel + the bar clearing? *Reco: a one-line record — a durable trace of the decision in the conversation.*
- **OD-2 (C):** spinner placement — a thin line at the ChatPane bottom (above the input), or in the topbar next to `connected`? *Reco: ChatPane bottom (where attention is); topbar is farther from the action.*
- **OD-3 (C):** label — generic `thinking…` vs mode-specific (`reasoning…` / `answering…` / `checking your read…`). *Reco: generic for v1 (the payload can carry the mode later).*

## §7. Definition of Done
- **A:** `parse_interpretation` forces `needs_confirm=True` for a terminal status (+ synthesizes a prompt if absent); soft statuses stay auto. Unit-tested (COMPLETE+needs_confirm=false → confirm forced + prompt present; in_progress → stays soft; model prompt preserved when given).
- **B:** a pending confirm renders in the `#confirm-bar` (revealed), distinct from chat lines; the input placeholder reflects a pending confirm; both clear on resolve; the `/y /n` routing + false-commit safety unchanged (existing routing tests stay green). Pilot test: dispatch a confirm `operator.question` → bar reveals + placeholder changes; resolve → bar hides.
- **C:** each LLM cycle publishes `assistant.thinking` start/stop (unit-asserted on the assistant); `tui_ws` forwards it; the TUI shows the spinner after 200 ms and auto-erases, never delaying input (Pilot test: dispatch start → (after delay) spinner present; stop → gone; input stays focusable throughout).
- 5 CI gates green; **operator re-smoke** (the live surface): the confirm reads as a clear ask, a close asks before committing, the spinner shows while thinking.

## §8. Test strategy
- **Unit (application):** `parse_interpretation` commit-status enforcement (`test_parser.py`); the cycle `assistant.thinking` emit + never-crash (`test_agent.py`/assistant tests).
- **Pilot (TUI):** confirm-bar reveal/hide + placeholder (`test_chat.py`); the thinking spinner show-after-delay/auto-erase + input stays responsive.
- **Regression:** the confirm `/y /n` routing, `resolve_confirm`, and `_pending` consume-on-confirmed-send are untouched and green.
- **Live (smoke):** the only acceptance for the real look/feel — re-run the interpretation + confirm checkpoints (K–N) against the new affordances.

## §9. Non-goals
- No change to the assistant's reasoning, the gate, the enricher, or the false-commit routing.
- Confirm stays NON-blocking (do NOT make it modal / hold the beacon).
- No chat-history persistence/replay (separate deferred follow-up).
- No "labeled as the assistant's read" WM styling (separate follow-up; auto-writes here are only the soft ones, which the operator already sees).

## §10. Risks
- **A:** a terminal status the model phrases outside `_COMMIT_STATUSES` (e.g. an invented synonym) still auto-writes — bounded (the prompt guides "COMPLETE"; the set covers the common synonyms; extend if the smoke shows a miss).
- **B:** the confirm bar must not steal focus from the input (keyboard-first) — it is display + a keybind target only; `/y /n` go through the existing Input.
- **C:** spinner flicker on rapid cycles — the 200 ms delay + idempotent start/stop guard it; the emit is never-crash so a thinking-signal failure can't break a cycle.
