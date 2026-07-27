# TUI chat hydration on connect — design

**Issue:** #41 · **Date:** 2026-07-27 · **Status:** approved by the operator

## Problem

Open the TUI mid-engagement and the Assistant Chat pane is **empty**, while Working Memory and Sessions come up populated. Observed during the 2026-07-27 live smoke: `engagements/vantage/chat_history.jsonl` held 13 turns and the pane showed none of them.

This is a *display* gap, not a memory gap. The transcript is persisted by `ChatRecorder` and injected into the assistant's own context by `build_history_reader`, so the assistant **remembers** the conversation across a TUI restart — the operator just can't see it. `ChatPane` composes an empty `RichLog` and only appends frames that arrive after connect.

The asymmetry is structural: on connect `tui_ws.py` sends exactly one replay frame, `working_memory_snapshot` (`tui_ws.py:76-80`). There is no equivalent for chat.

## Goal

On connect, the chat pane shows the recent conversation, the way a chat client does.

## Non-goals

- Re-summarising or recomputing context. This is display hydration only; the assistant's own context path (`build_history_reader` → two-window selection) is untouched.
- Scrollback beyond the hydrated window, search, or paging.
- Any change to what gets recorded.

## The shape of the change

**This is the existing connect-hydration pattern applied to a second thing.** Working memory already hydrates on connect; chat does not. No new concept is introduced.

```
TUI connects
  └─ tui_ws.py ──► working_memory_snapshot   (exists)
               ──► chat_history              (new)
               ──► live bus events           (exists)
```

Two files change:

| File | Addition |
|---|---|
| `redrobin/api/tui_ws.py` | `_build_chat_history()`, a mirror of `_build_initial_snapshot()`, plus one `_send` call |
| `redrobin/tui/textual_ui/chat.py` | `format_history_turn()`, a frame handler, and one `registry.register("chat_history")` |

No new facade, no new widget, no new pane. The only new state is a single `ChatPane._hydrated` flag, required because the TUI reconnects (see **Reconnect** below).

### Why no application-layer facade is needed

`tui_ws.py` already reaches the working-memory repo through `app.state` without importing infrastructure (`tui_ws.py:135-139`). The chat repo is on `app.state` too — `lifespan_helpers.py:428` sets `app.state.chat_history_repo`. The same pattern applies, so the import-linter contract *"presentation never imports infrastructure directly"* holds with no new indirection. Turn dicts come from `dataclasses.asdict` (stdlib).

### Frame contract

Type `chat_history`, payload `{"turns": [...]}`, each turn the `ChatTurn` fields already persisted: `ts`, `role`, `kind`, `text`, `correlation_id`. Sent immediately after `working_memory_snapshot`, before the live loop begins.

Turns come from a single `repo.tail(max_lines=50)` (the constant is named `_HISTORY_LINES`, since it caps lines) — **not** a full `MAX_TAIL_LINES` read sliced afterwards. `tail` already returns the parseable turns among the last N raw lines, which is exactly the window wanted, and reading 50 lines instead of 2000 keeps the connect path cheap.

Rendering uses `role`, `kind` and `text`. `ts` and `correlation_id` are carried because they are what a turn *is*, not because this feature reads them — `ts` is unused given the no-timestamp decision below, and `kind` already distinguishes `advisory` from `heads_up`, so `correlation_id` is not needed to pick the tag.

### The renderer

One pure function in `chat.py`, beside the six formatters already there:

```python
def format_history_turn(turn: dict) -> Text:
    label = "You" if turn["role"] == "operator" else "Red Robin"
    if turn["kind"] in _TAGGED_KINDS:      # advisory / heads_up / confirm
        label += f" · {turn['kind'].replace('_', '-')}"
    ...
```

The labelling rule already exists for the LLM slice (`application/chat/history.py:49`); this is that rule in the TUI's voice, **widened by one kind** — the LLM slice tags only `VOLUNTEERED_KINDS`, while the pane also tags `confirm`, because on screen a confirm must not read as a plain answer. It is not a dispatch table — a speaker label plus the stored text.

## Decisions and their reasoning

### Render history like live, with no dimming and no separator

The first draft marked history as visually distinct, arguing that an operator could mistake a two-hour-old advisory for one about the command they just ran. **The operator rejected this and was right.** Chat is chronological and append-only: new lines land at the bottom, which is where attention goes; reading a line near the top as "current" requires misreading scroll position. Claude Code resumes sessions exactly this way and does not confuse its users.

The residual difference from Claude Code is real but small, and does not justify ceremony: Red Robin's assistant speaks **unprompted** (advisories fire on beacon commands while the TUI is closed), so a reopened pane can carry content the operator has never seen — whereas a Claude Code transcript is entirely their own.

**Decided: no separator, no truncation marker, no dimming.** The thread simply starts.

### A historical advisory's `opsec` line carries no colour

`ChatRecorder` stores an advisory **flattened to text** — `advisory_text()` composes the action plus `why`/`opsec`/`conf` into one string (`recorder.py:32-48`). The structured `opsec_risk` value that drives `_RISK_COLORS` in the live renderer is not stored as a field.

Three options were weighed:

1. **Add `opsec_risk` to `ChatTurn`.** Rejected. Only advisories written *after* this ships would carry it; existing rows cannot be backfilled because the value was lost at write time. The result is a history where some advisories are coloured and others are not, for no reason visible to the operator — that reads as a bug, and buys an inconsistency in exchange for a persisted schema change.
2. **Re-parse the colour out of the stored text.** Rejected. Fragile string-scraping that breaks silently if `advisory_text`'s format changes.
3. **Render it uncoloured.** **Chosen.** Uniform and honest; the word `WARN` is still there, only without the highlight. This is the single line in the pane that differs from live.

### Subscribe before reading, as a forward-looking invariant

`tui_ws` subscribes to the bus *before* reading the transcript. The first draft of this spec claimed a turn written in that window would render twice, and told the reader to keep the order for that reason.

**The audit measured it and the duplicate does not currently fire.** There is no guaranteed yield point between `bus.subscribe()` and the read: the bus lock is uncontended, `_build_initial_snapshot` is fully synchronous, and `send_json` did not suspend even on a 900 KB frame under a real uvicorn server.

The order is still kept, and deliberately — but the justification is forward-looking, not a live hazard. If a yield point ever appears there, subscribe-then-read costs a visible, harmless duplicate, while read-then-subscribe would **silently lose** the turn. The code comment must say this, not assert a mechanism a future reader can measure as false and then delete along with the invariant.

## Degraded behaviour

`app.state.chat_history_repo` exists only when **both** `settings.assistant_enabled` is true and a working-memory repo was resolved (`lifespan_helpers.py:424-428`) — and the assistant is off by default. So the missing-repo path is the common one, not a defensive edge case:

- No repo on `app.state` → send an empty `turns` list. Correct: with the assistant off nothing ever wrote a transcript.
- Read failure → log, send an empty list. The pane starts empty, exactly today's behaviour.
- A repo returning anything that is not a `ChatTurn` → the same empty list. The mapping to dicts sits **inside** the guard, because the endpoint's outer `try` catches only `WebSocketDisconnect`: anything else raised here closes the socket and drops the TUI into its reconnect loop.
- Never block or fail the connect. A chat that cannot hydrate must not cost the operator their Working Memory and Sessions panes.

## Reconnect

The TUI auto-reconnects (`ws_worker.py:57` iterates `connect(...)` and continues on any exception), and every reconnect re-runs the endpoint from the top, re-sending both connect frames. The working-memory frame is idempotent because it **replaces** `app.snapshot`; the chat log is **append-only**, so an unguarded handler would re-append the whole thread on any ping timeout, daemon restart, or network blip.

**Hydration is therefore one-shot per pane.** The known cost: turns recorded while the socket was down are not rendered on reconnect. They remain on disk and in the assistant's context, and restarting the TUI shows them. Rendering only turns newer than the last hydrated one was considered and rejected — a live-rendered turn carries no `ts` in the TUI, so the watermark could not be advanced for it, and the next reconnect would duplicate everything rendered live in between.

## Window

The last **50 turns**, chronological. `select_context_turns`'s two-window rule is deliberately **not** reused: it exists so advisories cannot evict the dialogue thread from the model's prompt. For display, plain chronological order is what a chat client does and what the operator expects.

A module constant, not a `Setting` — matching the precedent of `MAX_TAIL_LINES` and `_MAX_TURN_CHARS` (YAGNI until an operator asks to tune it).

## Testing

- **Unit** — `format_history_turn` across the five kinds and both roles, including the tagged-label branch; the empty-turns case.
- **Unit** — the frame handler appends one line per turn, in order.
- **Integration** — connecting to `/v1/tui` yields a `chat_history` frame after `working_memory_snapshot`; with a populated transcript it carries the turns, with no repo it carries an empty list, and a repo that raises still yields a connect with an empty list.

Textual constraint to respect: **8.2.7 has no `Static.renderable`.** The repo's accessors are `str(widget.content)` for `Static` and `Strip.text` for `RichLog` (see `test_app_reactive.py:22`).

## Definition of Done

Three tiers. The first two are machine-checkable; the third is the acceptance, because `.claude/rules/development-workflow.md` lists the Textual TUI as **live surface** — CI cannot exercise it.

### 1. Behaviour (each item verifiable by someone who did not write it)

- [ ] Connecting to `/v1/tui` yields a `chat_history` frame, **after** `working_memory_snapshot` and **before** any live event.
- [ ] With a populated transcript, the frame carries the parseable turns among the last **50 lines**, in file order (oldest first) — **at most** 50, and fewer if any of those lines is malformed, because `tail` caps lines and drops unparseable ones without backfilling.
- [ ] Each turn renders with `You` for `role == "operator"` and `Red Robin` otherwise, plus a `· advisory` / `· heads-up` / `· confirm` suffix for those kinds. Dialogue occupies one line; an advisory body is multi-line by design.
- [ ] **A reconnect does not duplicate the thread.** The TUI auto-reconnects and the daemon re-sends the frame on every connect, so hydration is one-shot per pane.
- [ ] A hydrated advisory shows its stored body (action + `why` / `opsec` / `conf`). The `opsec` line is **uncoloured** — this is the intended difference from live, not a defect.
- [ ] **No separator, no dimming, no truncation marker.** The thread simply starts.
- [ ] With `app.state.chat_history_repo` absent (the assistant is disabled — the default), the frame carries `turns: []` and the pane starts empty, with no error and no traceback.
- [ ] A repo whose read raises still yields a successful connect with `turns: []`; the failure is logged.
- [ ] **No regression:** Working Memory and Sessions still populate on connect, and live frames still render after hydration.

### 2. Quality gates

- [ ] Five CI gates green: `ruff check`, `ruff format --check`, `mypy redrobin/domain redrobin/application`, `lint-imports` (4/4 contracts), `pytest tests/`.
- [ ] `pytest adapters/` green.
- [ ] Test count reconciles exactly against the tests added — the arithmetic stated, not asserted.
- [ ] TDD: every behavioural test was seen failing before its implementation.
- [ ] Atomic conventional commits, no AI co-author trailer.
- [ ] Coverage as listed under **Testing** above: the renderer across both roles and all five kinds, the handler's ordering, and the three integration cases (populated / no repo / raising repo).

### 3. Operator acceptance (the only acceptance for the visible result)

Run with the assistant enabled (`REDROBIN_ASSISTANT_ENABLED=1`) against the `vantage` engagement:

- [ ] Open the TUI, ask the assistant something, get an answer. Quit with `q`. **Reopen the TUI — the exchange is there.**
- [ ] The rehydrated thread reads as a conversation: no duplicated lines, no lines out of order, no visual noise between old and new.
- [ ] Send a new message. It appends below the hydrated thread, in the same style.
- [ ] With an advisory in the transcript, its body is present and legible.
- [ ] **Restart the daemon with the TUI still open.** It reconnects on its own and the thread must NOT be re-appended — a quit-and-reopen check does not exercise this path.
- [ ] The pane's scroll position is usable on open — the operator sees the most recent turns, not the top of the history.

## Out of Done — explicitly not required to ship

- Colouring a historical advisory's `opsec` value.
- Any indication that the thread is truncated.
- Scrollback beyond the 50-turn window, search, or paging.
- Normalising the transcript's storage model (see below).

## Deliberately not in scope

**The storage smell this exposes.** `ChatRecorder` persists a *view* (composed text) rather than the *data* (the advisory's fields), because the application layer cannot import the TUI renderer. Normalising that is a change to the transcript model and the assistant's reader — its own ticket, not this one.
