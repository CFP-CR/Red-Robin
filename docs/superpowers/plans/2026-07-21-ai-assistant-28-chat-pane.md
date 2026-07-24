# TUI Chat pane + bidirectional WS — #28 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the operator INPUT surface for the assistant — a **persistent Chat pane** (operator ask/answer + the pending-confirm affordance) that **replaces the center `Verdicts / Events` pane**, plus the **bidirectional TUI↔daemon WS** it needs (the WS is send-only today). This makes the Task-5 conversational backend and the Task-6 propose-and-confirm operator-usable, and carries their live-surface smoke.

**Operator decisions (2026-07-21):** (1) the Chat pane goes in the **CENTER, replacing the pre-flight `Verdicts / Events` pane** (the advisory assistant supersedes the synchronous pre-flight gate, so its display cedes the center to the chat). (2) Follow the **existing Textual TUI conventions** (reactive+`watch_*`, `@REGISTRY.register` frames, `Container` panes + `theme.tcss`, `push_screen` modals) AND **industry UX best practices** — no divergent one-off style. (3) Pending confirms render **inline in the chat** with a y/n affordance.

**Architecture:** Two directions, both new. **Daemon** (`api/tui_ws.py`): today it only sends (`while True: send_json`); add a concurrent **receive** loop that reads the operator's messages from the socket, tags them `source:"operator"`, and publishes `operator.question`/`operator.answer` on the bus (which the Task-5/6 assistant already consumes). **TUI** (`ws_worker.py` + `app.py`): the worker exposes a send handle; a new `ChatPane` (a `RichLog` transcript + an `Input`) replaces `#events`; `@REGISTRY.register` handlers render the assistant's `operator.answer` (its reply) and its `operator.question` confirm proposals (context_refs `milestone:<id>`). Loop-avoidance via the `source` tag (the TUI skips its own echoed messages; assistant-authored messages carry `source:"assistant"`).

**Tech Stack:** Textual (widgets + `theme.tcss`, `Pilot` test harness), `websockets` (client) + FastAPI `WebSocket` (server), `pytest` + `pytest-asyncio`, `uv`, `ruff`, `mypy` (gradual on `api`/`tui`), `import-linter`.

## Global Constraints

- **The 5 CI gates must pass** (`uv run --no-sync ...` if a bare `uv run` fails on a venv lock): `ruff check .` · `ruff format --check .` · `mypy redrobin/domain redrobin/application` · `lint-imports --config importlinter.cfg` · `pytest tests/ -v --tb=short`. (mypy strict covers domain+application; `api`/`tui` are gradual — but keep new code typed.)
- **ruff E402**; **TDD (red→green)**; **atomic conventional commits, NO AI co-author trailer**.
- **Follow the existing TUI conventions** (the memory `ux-ui-follow-existing-tui-conventions`): reactive state + `watch_*`; frame handlers via `@REGISTRY.register("<type>")` bound with a `register_*` binder (avoid circular imports); panes = `Container(id=...)` + `.border_title` + one widget, styled in `theme.tcss` with the `$`-semantic tokens; keyboard-first `BINDINGS`; the render function is a PURE Rich renderer (unit-testable like `format_recommendation`).
- **Never-crash**: a bad WS frame (either direction) is logged + dropped, never kills the socket/worker (mirror `ws_worker.handle_raw` + the daemon `try/except`); the daemon receive loop never crashes the send loop.
- **Presentation-layer boundary** (import-linter): `tui/` and `api/` reach infrastructure only via application facades — the daemon WS publishes on `app.state.event_bus` (already available); the TUI imports only `frames`/widgets (no infra).
- **Acceptance form (spec §8 / `development-workflow.md` §5):** the deterministic pieces — the daemon receive→publish (with the `source` tag), the frame handlers/renderers, the ChatPane composition + Input routing — are CI-tested (FastAPI/starlette WS test client + Textual `Pilot`). The **live surface** (the real chat round-trip over a live beacon, a rendered pending confirm the operator resolves) → an **operator smoke**, run in the **batched pre-`dev`-merge session** (with the Task 2/4/6 checkpoints).

## Design Decisions (locked in-plan)

- **D1 — ChatPane REPLACES the center `#events` pane** (operator decision). `compose()` swaps the `EventsLog` Container for a `ChatPane` at the same `#events`/3fr slot. The verdict/event STREAM display is superseded; the topbar verdict COUNT stays (it reads `app.events`, still updated by the `opsec.verdict` handler — that handler + reactive are untouched; only the `EventsLog` widget is removed from `compose`). `theme.tcss` `#events` styling is repurposed for the chat.
- **D2 — Bidirectional WS via a source tag.** The daemon `tui_ws` runs the existing send loop AND a new receive loop concurrently (`asyncio.gather`/`TaskGroup`). The receive loop reads `{type, ...}` from the socket, stamps `payload["source"]="operator"`, and `bus.publish({type, payload})` (only for the allowlisted `operator.question`/`operator.answer` types — nothing else the TUI could inject). The assistant's own publishes carry no source (or `assistant`); the reducer/other consumers ignore the extra field (internal-bus-extra-field precedent, `wire-protocol.md`). The TUI frame handlers RENDER only `source != "operator"` (assistant-authored) messages — so the operator's own messages, which fan back through the bus, are not double-rendered (the TUI echoes them locally on send).
- **D3 — Loop-avoidance (the assistant already publishes AND consumes these events — Task 6 D5).** The Task-6 `is_own_proposal_question` skip + `resolve_confirm` id-matching already prevent the ASSISTANT from looping. The TUI adds the symmetric guard: it renders assistant-authored (`source!="operator"`) messages only, and the daemon only re-publishes TUI messages of the two allowlisted types — so a TUI message can never be interpreted by the daemon as anything but an operator ask/answer.
- **D4 — `question_id`.** An operator ASK: the TUI mints a per-session `question_id` (a monotonic counter, `"q<N>"` — no RNG needed, deterministic for tests) and puts it in the `operator.question` bus payload (internal field, TUI→bus, never `/v1/events`); the assistant's `operator.answer` echoes it → the TUI matches the reply to the ask. A CONFIRM answer: the `question_id` is the pending `milestone_id` (from the proposal's `context_refs=["milestone:<id>"]`), so Task-6 `resolve_confirm` matches it.
- **D5 — Input routing (ask vs confirm-answer): an EXPLICIT `/y` `/n` token, NOT a natural-yes/no heuristic** (audit fix — a natural "ok"/"no" heuristic would silently COMMIT/DROP engagement state from an ordinary short word). One `Input`. On submit: a confirm answer is ONLY the exact slash token `/y` `/yes` `/n` `/no` when a confirm is pending → `operator.answer(question_id=<oldest pending milestone_id>, text="yes"|"no")`; **everything else is always a question** → `operator.question(question_id="q<N>")`. So a bare `ok`/`no`/`no creds?` can never be mistaken for a confirm — it's a question. The pending confirm renders with an explicit `[reply /y or /n]` hint. The daemon's `resolve_confirm` still authorizes the commit/drop; the slash token just makes the client routing collision-free. (A dedicated y/n keybinding is deferred polish — it conflicts with the focused Input.)
- **D6 — Acceptance = deterministic CI + a batched smoke** (D6 above). No new smoke session — the #28 checkpoints join the existing batched runbook.

## File Structure

**Create:**
- `redrobin/tui/textual_ui/chat.py` — the `ChatPane` widget (`RichLog` + `Input`), the pure Rich renderers (`format_answer`, `format_confirm`, `format_operator_echo`), the `@REGISTRY.register` handlers, and `register_chat(registry)` binder (mirrors `advisor.py`).
- `tests/unit/test_tui_ws_receive.py` — the daemon receive→publish (source tag, allowlist, never-crash) via the starlette WS test client.
- `tests/unit/tui_textual/test_chat.py` — the ChatPane composition, the renderers, the frame handlers, the Input routing (Pilot).

**Modify:**
- `redrobin/api/tui_ws.py` — add the concurrent receive loop (publish allowlisted operator.* with `source:"operator"`).
- `redrobin/tui/textual_ui/ws_worker.py` — expose a send handle (store `target._ws_send` on connect; clear on disconnect) so the app can send.
- `redrobin/tui/textual_ui/app.py` — `compose()` swaps the `#events` EventsLog for the `ChatPane` (which BECOMES the `#events` pane, id `events`, `border_title="Assistant Chat"` — one widget per pane, per convention); `register_chat(REGISTRY)`; an `async def send_operator_message(...)`; a `c` binding to focus the chat input; **remove the `query_one("#events-log", EventsLog).render_events(...)` line from `watch_events`** (keep `_refresh_topbar()` — the verdict COUNT survives) **and drop `EventsLog` from the `panels` import** (else NameError/F401).
- **`redrobin/tui/textual_ui/commands.py`** — the command-palette entry `("Focus Verdicts / Events", _focus("#events-log"), ...)` (line ~30) becomes a **runtime `NoMatches` crash** once `#events-log` is gone. Repoint it: `("Focus Assistant Chat", _focus("#chat-input"), ...)`.
- **Existing tests that BREAK on the `EventsLog` removal — update them IN THE SAME (28.2) commit so CI stays green per commit:** `tests/unit/tui_textual/test_app_phase2.py` (drop the `#events-log` render-assert lines + the `EventsLog` import), `test_app_phase3.py` (delete/repoint the `#events-log` overflow test to `#chat-log`), `test_navigation.py` (the tab-order list + the "Focus Verdicts" command-focus test → chat), `test_ws_worker.py` (the `_FakeWS` needs a `send` attr + `target` must be a real object with `__dict__`, not bare `object()`).
- `redrobin/tui/textual_ui/theme.tcss` — delete the now-dead `#events-log` block; add `#chat-log` (scroll) + `#chat-input` (`dock: bottom; height: 3`).
- `scripts/smoke/rr-assistant-smoke-runbook.md` — the `[#28]` conversational + confirm checkpoints (batched) + relabel the stale `[later task]` conversational/learning bullets.

**Untouched (verify):** the assistant backend (Tasks 5/6), the reducer, `panels.py`'s `EventsLog` CLASS + `apply_verdict`/`verdict_counts` (the class stays for `test_panels.py`'s own harness; the topbar count still uses `apply_verdict`/`verdict_counts` over `app.events`), the `opsec.verdict` handler + `app.events` reactive.

**Focus/UX (audit):** set `RichLog(id="chat-log")` `can_focus = False` so Tab reaches only the `Input` (not the non-interactive scrollback) — the cycle becomes `sessions-table → chat-input → memory-panel`. Initial focus stays `sessions-table` (a `c` binding jumps to the chat input — keyboard-first, without disrupting boot focus).

---

### Task 28.1: Daemon bidirectional WS (receive → publish, source-tagged)

Add the missing TUI→daemon direction: the operator's messages become bus events the assistant already consumes.

**Files:**
- Modify: `redrobin/api/tui_ws.py`
- Test: create `tests/unit/test_tui_ws_receive.py`

**Interfaces:**
- The `/v1/tui` socket now reads operator messages AND sends bus events concurrently. Allowlist: only `operator.question` / `operator.answer` from the TUI are published (each stamped `payload["source"]="operator"`).

- [ ] **Step 1: Write the failing test** (starlette WS test client)

```python
# tests/unit/test_tui_ws_receive.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_operator_question_from_tui_is_published_with_source(monkeypatch, tmp_path):
    # A message the TUI sends over /v1/tui is published on the bus as operator.question
    # tagged source="operator" (so the assistant consumes it and the TUI won't re-render it).
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message  # pure helper (below)

    bus = EventBus()
    sub = await bus.subscribe()
    await _publish_operator_message(bus, {"type": "operator.question", "text": "path to DA?", "question_id": "q1"})
    evt = sub.get_nowait()
    assert evt["type"] == "operator.question"
    assert evt["payload"]["text"] == "path to DA?"
    assert evt["payload"]["question_id"] == "q1"
    assert evt["payload"]["source"] == "operator"


@pytest.mark.asyncio
async def test_non_allowlisted_type_from_tui_is_dropped():
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message

    bus = EventBus()
    sub = await bus.subscribe()
    # the TUI must NOT be able to inject arbitrary events (e.g. engagement.milestone)
    await _publish_operator_message(bus, {"type": "engagement.milestone", "milestone_id": "x", "status": "COMPLETE"})
    assert sub.empty()


@pytest.mark.asyncio
async def test_publish_operator_message_never_raises():
    from redrobin.application.event_bus import EventBus
    from redrobin.api.tui_ws import _publish_operator_message

    bus = EventBus()
    await _publish_operator_message(bus, "not a dict")  # type: ignore[arg-type]
    await _publish_operator_message(bus, {"no": "type"})
    # no assertion needed — the point is it does not raise
```

ALSO add TWO integration tests over a real socket (mirror the fixture in the existing `tests/**/test_tui_ws*.py` — `starlette` `TestClient.websocket_connect(f"/v1/tui?token=...")`), because the concurrent `_send`/`_receive` refactor + the never-crash-on-bad-frame path are NOT covered by the helper tests:
- **round-trip:** connect, drain the initial snapshot, `ws.send_json({"type":"operator.question","text":"path?","question_id":"q1"})`, then read frames until an `operator.question` arrives (it fans back out the same socket) and assert `payload["source"]=="operator"` and `payload["question_id"]=="q1"`.
- **never-crash (C6 guard):** connect, `ws.send_text("not json")`, then publish a normal bus event (e.g. via `app.state.event_bus.publish`) and assert the socket STILL delivers it — with the un-guarded receive loop this test fails, which is the point.

- [ ] **Step 2: Run test to verify it fails** — `uv run --no-sync pytest tests/unit/test_tui_ws_receive.py -v` → FAIL (no `_publish_operator_message`).

- [ ] **Step 3: Write the implementation** (`redrobin/api/tui_ws.py`)

Add the pure helper + the concurrent receive loop. The allowlist + source tag live in the helper (unit-testable without a socket):

```python
import asyncio

_TUI_ALLOWED = {"operator.question", "operator.answer"}


async def _publish_operator_message(bus, msg) -> None:
    """Publish an operator-originated TUI message on the bus, if it is an allowlisted
    operator.* type, stamped source='operator' (so the assistant consumes it and the
    TUI does not re-render its own echo). Never raises (a bad frame is dropped)."""
    try:
        if not isinstance(msg, dict):
            return
        etype = msg.get("type")
        if etype not in _TUI_ALLOWED:
            return
        payload = {k: v for k, v in msg.items() if k != "type"}
        payload["source"] = "operator"
        await bus.publish({"type": etype, "payload": payload})
    except Exception:
        logger.exception("tui_ws: failed to publish operator message")
```

Refactor `tui_ws` to run send + receive concurrently:

```python
@router.websocket("/v1/tui")
async def tui_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = None
    try:
        queue = await bus.subscribe()
        snapshot_payload = await _build_initial_snapshot(websocket.app)
        await websocket.send_json({"type": "working_memory_snapshot", "payload": snapshot_payload})

        async def _send() -> None:
            while True:
                event = await queue.get()
                if event.get("_type") == "_close":
                    return
                await websocket.send_json(event)

        async def _receive() -> None:
            while True:
                try:
                    msg = await websocket.receive_json()
                except WebSocketDisconnect:
                    return  # clean disconnect — end the receive side
                except Exception:
                    logger.warning("tui_ws: dropped a bad inbound frame")
                    continue  # a malformed frame must NOT kill the socket (never-crash)
                await _publish_operator_message(bus, msg)

        send_task = asyncio.create_task(_send())
        recv_task = asyncio.create_task(_receive())
        _, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        # await the cancelled task(s) so their exceptions are retrieved (no
        # "Task exception was never retrieved" warning on disconnect).
        await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)
```

- [ ] **Step 4: Run test to verify it passes** — `uv run --no-sync pytest tests/unit/test_tui_ws_receive.py -v` → PASS. Also run the existing `tests/**/*tui_ws*` / WS tests to confirm the send path is unregressed.

- [ ] **Step 5: Commit** — `git add redrobin/api/tui_ws.py tests/unit/test_tui_ws_receive.py && git commit -m "feat(tui-ws): bidirectional /v1/tui — publish allowlisted operator.* from the TUI (source-tagged)"`

---

### Task 28.2: TUI send handle + the ChatPane widget

The TUI side: a send path from the worker to the app, and the ChatPane replacing `#events`.

**Files:**
- Modify: `redrobin/tui/textual_ui/ws_worker.py`, `redrobin/tui/textual_ui/app.py`, `redrobin/tui/textual_ui/theme.tcss`; create `redrobin/tui/textual_ui/chat.py` (the widget + renderers only; handlers in 28.3)
- Test: create `tests/unit/tui_textual/test_chat.py`

**Interfaces:**
- `ws_worker.ws_loop` stores a send handle on `target` when connected (`target._ws_send = ws.send`; `None` on disconnect).
- `ChatPane(Container)` — a `RichLog(id="chat-log")` + `Input(id="chat-input")`; `append_line(Text)` appends to the log.
- `RedRobinApp.send_operator_message(msg: dict) -> None` — JSON-sends via `_ws_send` if connected (else a "disconnected" line).

- [ ] **Step 1: Write the failing test** (Pilot)

```python
# tests/unit/tui_textual/test_chat.py
from __future__ import annotations

import pytest

from redrobin.tui.textual_ui.chat import ChatPane, format_answer, format_confirm, format_operator_echo


def test_format_answer_labels_assistant():
    t = format_answer({"text": "kerberoast then crack", "citations": ["kb:x"]})
    s = t.plain
    assert "Red Robin" in s and "kerberoast then crack" in s


def test_format_confirm_shows_yn_affordance():
    t = format_confirm({"text": "Objective DA -> COMPLETE?", "context_refs": ["milestone:obj1"]})
    s = t.plain
    assert "CONFIRM" in s and "Objective DA -> COMPLETE?" in s
    assert "/y" in s.lower() and "/n" in s.lower()


def test_format_operator_echo_labels_you():
    assert "You" in format_operator_echo("what's my path to DA?").plain


@pytest.mark.asyncio
async def test_chat_pane_composes_and_appends():
    from textual.app import App, ComposeResult
    from rich.text import Text

    class _Harness(App):
        def compose(self) -> ComposeResult:
            yield ChatPane(id="events")

    async with _Harness().run_test() as pilot:
        pane = pilot.app.query_one(ChatPane)
        pane.append_line(Text("hello"))
        # the RichLog + Input mounted, no exception
        pane.query_one("#chat-input")
        pane.query_one("#chat-log")
```

- [ ] **Step 2: Run to verify red** — FAIL (no `chat` module).

- [ ] **Step 3: Write the implementation**

`ws_worker.ws_loop` — store the send handle (add just after `set_state("connected")` and clear it in the reconnect/return paths):

```python
    async for ws in connect(full, ping_interval=20, ping_timeout=20):
        if set_state:
            set_state("connected")
        setattr(target, "_ws_send", ws.send)  # the app sends operator messages via this
        try:
            async for raw in ws:
                ...  # UNCHANGED
        except Exception as exc:
            setattr(target, "_ws_send", None)
            ...  # UNCHANGED reconnect
            continue
        setattr(target, "_ws_send", None)
        if stop_event.is_set():
            return
```

`redrobin/tui/textual_ui/chat.py` (widget + renderers; handlers land in 28.3):

```python
"""Assistant Chat pane (#28) — the operator's ask/answer surface + inline pending
confirms. Replaces the center Verdicts/Events pane (operator decision 2026-07-21).
Renderers are pure Rich (unit-testable, like advisor.format_recommendation)."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog

_ACCENT = "#58a6ff"
_MUTED = "#8b949e"
_CONFIRM = "#e3b341"


class ChatPane(Container):
    """A scrollback (RichLog) + an Input. `border_title` set by app.compose."""

    def compose(self) -> ComposeResult:
        log = RichLog(id="chat-log", highlight=False, markup=False, wrap=True, min_width=0)
        log.can_focus = False  # only the Input is a tab stop (the scrollback isn't interactive)
        yield log
        yield Input(id="chat-input", placeholder="Ask the assistant…  (/y or /n to a confirm)")

    def append_line(self, text: Text) -> None:
        try:
            self.query_one("#chat-log", RichLog).write(text)
        except Exception:
            pass


def format_operator_echo(text: str) -> Text:
    t = Text()
    t.append("You  ", style=f"bold {_ACCENT}")
    t.append(text)
    return t


def format_answer(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("Red Robin  ", style=f"bold {_ACCENT}")
    t.append(str(payload.get("text") or ""))
    cites = payload.get("citations") or []
    if cites:
        t.append("\n  refs ", style=_MUTED)
        t.append(", ".join(str(c) for c in cites), style=_MUTED)
    return t


def format_confirm(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("CONFIRM  ", style=f"bold {_CONFIRM}")
    t.append(str(payload.get("text") or ""))
    t.append("   [reply /y or /n]", style=_MUTED)
    return t


__all__ = ["ChatPane", "format_answer", "format_confirm", "format_operator_echo"]
```

`app.py` — the ChatPane BECOMES the `#events` pane (one widget per pane, per convention):

```python
# compose(): replace the events Container block with the ChatPane AS the pane:
            chat = ChatPane(id="events")
            chat.border_title = "Assistant Chat"
            yield chat
```
Also: drop `EventsLog` from the `panels` import; **remove the `query_one("#events-log", EventsLog).render_events(...)` line from `watch_events`** (keep the `_refresh_topbar()` — the verdict count survives); add `("c", "focus_chat", "Chat")` to `BINDINGS` + `def action_focus_chat(self) -> None: self.query_one("#chat-input").focus()` (guarded). Keep `apply_verdict`/`verdict_counts` imports — the `opsec.verdict` handler is unchanged.

Add the send method + import `ChatPane`, `register_chat`, `json`:

```python
    async def send_operator_message(self, msg: dict) -> None:
        """Send an operator message (operator.question/answer) to the daemon over
        the WS. Echoes locally is the caller's job; this only transports."""
        send = getattr(self, "_ws_send", None)
        if send is None:
            self._chat_line(Text("(not connected — message not sent)", style="#8b949e"))
            return
        try:
            await send(json.dumps(msg))
        except Exception:
            logger.exception("tui: failed to send operator message")
```

`theme.tcss` — repurpose `#events` for the chat (the `#events` Container keeps its 3fr width + round border; style the inner widgets):

```css
/* #28 Chat pane (replaces the Verdicts/Events pane). */
#chat-log { overflow-x: hidden; overflow-y: auto; height: 1fr; }
#chat-input { dock: bottom; height: 3; border: round $panel-lighten-2; }
```

- [ ] **Step 4: Run to verify green** — `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py tests/unit/tui_textual/test_app_layout.py -v` (the layout test may need the `#events` title/child updated — adjust it to expect the ChatPane, since the pane is intentionally replaced).

- [ ] **Step 5: Commit** — `git add redrobin/tui/textual_ui/{ws_worker.py,app.py,theme.tcss,chat.py} tests/unit/tui_textual/test_chat.py [+ any updated layout test] && git commit -m "feat(tui): ChatPane replaces the Verdicts/Events pane + a WS send handle"`

---

### Task 28.3: Frame handlers + Input routing (ask vs confirm)

Wire the conversation: render the assistant's replies/confirms; route the operator's Input.

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py`, `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_chat.py` (append)

**Interfaces:**
- `@REGISTRY.register("operator.answer")` → render `format_answer` (skip `source=="operator"`).
- `@REGISTRY.register("operator.question")` → if a confirm proposal (`context_refs` has `milestone:`) render `format_confirm` + track the pending id; else (a plain operator ask that fanned back) skip.
- `register_chat(registry)` binder. The ChatPane tracks `_pending: list[str]` (milestone ids awaiting a confirm) + a per-session `_q_counter`; `on_input_submitted` routes.

- [ ] **Step 1: Write the failing tests** (Pilot + handler-direct)

```python
# append to tests/unit/tui_textual/test_chat.py
from redrobin.tui.textual_ui.chat import register_chat, is_confirm_question, next_operator_message
from redrobin.tui.textual_ui.frames import Registry


def test_is_confirm_question():
    assert is_confirm_question({"context_refs": ["milestone:obj1"]}) is True
    assert is_confirm_question({"context_refs": []}) is False
    assert is_confirm_question({"text": "hi"}) is False


def test_next_operator_message_routes_ask_vs_confirm():
    # no pending -> a new question with a minted id
    kind, msg = next_operator_message("what's my path to DA?", pending=[], q_index=0)
    assert kind == "question" and msg["type"] == "operator.question" and msg["question_id"] == "q0"
    # pending confirm + the EXPLICIT /y token -> an answer to the oldest pending
    kind, msg = next_operator_message("/y", pending=["obj1"], q_index=3)
    assert kind == "answer" and msg["type"] == "operator.answer" and msg["question_id"] == "obj1" and msg["text"] == "yes"
    kind, msg = next_operator_message("/n", pending=["obj1"], q_index=3)
    assert kind == "answer" and msg["text"] == "no"
    # an ORDINARY short word is NEVER a confirm answer, even with a pending -> a question
    # (this is the false-commit fix: "ok" must not commit the milestone)
    kind, msg = next_operator_message("ok", pending=["obj1"], q_index=3)
    assert kind == "question"
    kind, msg = next_operator_message("no creds?", pending=["obj1"], q_index=3)
    assert kind == "question"


from textual.app import App, ComposeResult
from textual.widgets import RichLog


class _ChatHarness(App):
    def compose(self) -> ComposeResult:
        yield ChatPane(id="events")  # the ChatPane IS the #events pane


@pytest.mark.asyncio
async def test_operator_answer_renders_assistant_skips_own_echo():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        # our own echo (source=operator) is skipped; the assistant reply renders
        reg.dispatch(target=pilot.app, frame={"type": "operator.answer", "payload": {"text": "hi", "source": "operator"}})
        reg.dispatch(target=pilot.app, frame={"type": "operator.answer", "payload": {"text": "kerberoast", "source": "assistant"}})
        assert len(pilot.app.query_one("#chat-log", RichLog).lines) == 1  # broken skip -> 2


@pytest.mark.asyncio
async def test_operator_question_confirm_renders_and_tracks_pending():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        reg.dispatch(target=pilot.app, frame={"type": "operator.question",
            "payload": {"text": "Obj DA COMPLETE?", "context_refs": ["milestone:obj1"], "source": "assistant"}})
        pane = pilot.app.query_one("#events", ChatPane)
        assert pane._pending == ["obj1"]
        assert len(pane.query_one("#chat-log", RichLog).lines) == 1
        # an operator-sourced echo must NOT track or render
        reg.dispatch(target=pilot.app, frame={"type": "operator.question",
            "payload": {"text": "x", "context_refs": ["milestone:z"], "source": "operator"}})
        assert pane._pending == ["obj1"]
```

_Note (audit D-d): a test that DRIVES `on_input_submitted` must use `RedRobinApp(start_worker=False)` (which defines `send_operator_message` + leaves `_ws_send=None` → the guarded "not connected" line), NOT a bare `App` harness (which has no `send_operator_message` → AttributeError)._

- [ ] **Step 2: Run to verify red** — FAIL.

- [ ] **Step 3: Write the implementation** (`chat.py`)

```python
def is_confirm_question(payload: dict[str, Any]) -> bool:
    refs = payload.get("context_refs")
    return isinstance(refs, list) and any(str(r).startswith("milestone:") for r in refs)


def _milestone_id(payload: dict[str, Any]) -> str:
    for r in payload.get("context_refs") or []:
        if str(r).startswith("milestone:"):
            return str(r).split(":", 1)[1]
    return ""


def next_operator_message(text: str, *, pending: list[str], q_index: int) -> tuple[str, dict[str, Any]]:
    """Route the operator's submitted text. A confirm answer is ONLY an EXPLICIT slash
    token (/y /yes /n /no) while a confirm is pending — so an ordinary short word
    ('ok', 'no creds?') is NEVER mistaken for a confirm and can never silently commit/
    drop engagement state. Everything else is a question with a minted id. The daemon's
    resolve_confirm still authorizes the commit/drop; the slash token makes routing
    collision-free (the minted 'q<N>' also never collides with a semantic milestone id)."""
    t = text.strip()
    low = t.lower()
    if pending and low in ("/y", "/yes", "/n", "/no"):
        answer = "yes" if low in ("/y", "/yes") else "no"
        return "answer", {"type": "operator.answer", "question_id": pending[0], "text": answer}
    return "question", {"type": "operator.question", "question_id": f"q{q_index}", "text": t}


def _chat(app: Any) -> "ChatPane | None":
    from textual.css.query import NoMatches

    try:
        return app.query_one("#events", ChatPane)  # the ChatPane IS the #events pane
    except NoMatches:
        return None  # pre-mount — mirror advisor._on_recommendation's guard (never-crash)


def _on_answer(app: Any, payload: dict[str, Any]) -> None:
    if payload.get("source") == "operator":
        return  # our own echo fanned back — don't re-render
    pane = _chat(app)
    if pane is not None:
        pane.append_line(format_answer(payload))


def _on_question(app: Any, payload: dict[str, Any]) -> None:
    if payload.get("source") == "operator" or not is_confirm_question(payload):
        return  # a plain operator ask (its own echo) — skip; only assistant confirms render
    pane = _chat(app)
    if pane is None:
        return
    pane.append_line(format_confirm(payload))
    mid = _milestone_id(payload)
    if mid and mid not in pane._pending:
        pane._pending.append(mid)


def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)
```

Add `_pending: list[str]` + `_q_counter: int` state to `ChatPane.__init__` and an `on_input_submitted` that echoes + routes + sends:

```python
class ChatPane(Container):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending: list[str] = []
        self._q_counter = 0

    ...  # compose(), append_line() as before

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        kind, msg = next_operator_message(text, pending=self._pending, q_index=self._q_counter)
        if kind == "question":
            self._q_counter += 1
        else:  # answer -> consume the pending it targeted
            if msg["question_id"] in self._pending:
                self._pending.remove(msg["question_id"])
        self.append_line(format_operator_echo(text))
        await self.app.send_operator_message(msg)  # type: ignore[attr-defined]
```

In `app.py`, add `register_chat(REGISTRY)` next to `register_advisor(REGISTRY)`.

- [ ] **Step 4: Run to verify green** — `uv run --no-sync pytest tests/unit/tui_textual/ -v` (chat + the existing TUI tests unregressed).

- [ ] **Step 5: Commit** — `git add redrobin/tui/textual_ui/{chat.py,app.py} tests/unit/tui_textual/test_chat.py && git commit -m "feat(tui): chat frame handlers (assistant reply + inline confirm) + ask/confirm Input routing"`

---

### Task 28.4: Smoke checkpoint + the 5 CI gates

**Files:** Modify: `scripts/smoke/rr-assistant-smoke-runbook.md`

- [ ] **Step 1: Append the `## [#28] Conversational + confirm affordance` checkpoints** — ask a question in the Chat pane → a grounded answer renders; push back ("too loud") → confirm a lesson is captured (via the DB/`get_lessons`) + applied next cycle; a Task-6 close/commit → a pending CONFIRM renders inline with `[reply y/n]`, resolve it (yes → objective COMPLETE in the WM panel; no → dropped); the beacon never held; a plain question never resolves a pending confirm. Also update the runbook's stale `[later task]` conversational/learning bullets to "done via #28".
- [ ] **Step 2: Commit** — `docs(smoke): add #28 chat + confirm operator checkpoints (conversational/learning/interpretation live surface)`
- [ ] **Step 3: Run the 5 CI gates** — all green; Textual/WS tests pass.

---

## Acceptance / Definition of Done (#28)

- [ ] **5 CI gates green.**
- [ ] **TDD red→green** — daemon receive→publish + allowlist + source tag + never-crash (28.1); ChatPane composition + renderers + send handle (28.2); frame handlers + Input routing (28.3).
- [ ] **Bidirectional WS** — the operator's chat message reaches the assistant (published operator.question/answer, source-tagged); the assistant's reply/confirm renders in the Chat pane. Only allowlisted operator.* is published from the TUI (no arbitrary event injection).
- [ ] **Conversational + confirm operator-usable** — ask/answer works; a pending confirm renders inline with a y/n affordance and resolves non-blocking (the beacon never held).
- [ ] **No loop / no double-render** — source tag: the TUI renders assistant-authored messages only; the assistant's Task-6 guards are untouched.
- [ ] **Follows the TUI conventions** — reactive/handler/pane/theme patterns; pure Rich renderers; keyboard-first. The Verdicts/Events pane is replaced (operator decision); the topbar verdict count is preserved.
- [ ] **Never-crash** — a bad frame (either direction) is dropped, the socket/worker survives.
- [ ] **Acceptance form** — deterministic (WS + Pilot) → CI gates + tests; live-surface (real chat over a live beacon, a rendered confirm resolved) → the batched operator smoke.
- [ ] **Atomic conventional commits, no AI co-author trailer.**

## Documented limitations / deferred

- **Dedicated y/n keybinding for confirms** is deferred (conflicts with the focused Input) — the type-to-answer path is the plain first pass (D5).
- **Multiple simultaneous pending confirms** resolve oldest-first via the Input heuristic; a per-confirm selector is polish.
- **The verdict/event STREAM display** is superseded by the chat (operator decision); the topbar verdict COUNT remains. Relocating the stream (if ever wanted) is a separate change.
- **`operator.question` wire `question_id`** is carried as an internal bus field (TUI→bus, never `/v1/events`) — the Task-5 D7 constraint, resolved here for the TUI producer. **Invariant** (loop-safety): a minted ask id `q<N>` never equals a semantic `milestone_id`, so an assistant answer can't resolve a confirm and an operator ask can't false-match a pending.
- **No in-chat outcome line after a confirm resolves** — the commit/drop shows in the Working-Memory panel (the reducer re-emits the snapshot); a `committed`/`dropped` transcript line is deferred polish (a blanket `engagement.milestone` TUI handler would also render noisy soft auto-writes).
- **Audit fixes folded in** (3 Opus auditors): the `EventsLog`-removal blast radius (`commands.py` + `test_app_phase2/3` + `test_navigation` + `test_ws_worker` all updated IN the 28.2 commit — green per commit), the daemon receive-loop never-crash, the false-commit-proof `/y`/`/n` routing (an ordinary word never resolves a confirm), the `_chat` `NoMatches` guard, ChatPane-is-`#events`, `chat-log` `can_focus=False` + the `c`-to-chat binding, real-asserting Pilot tests + the WS integration/never-crash tests.

## Self-Review (author checklist — completed)

1. **Convention coverage:** reactive+`watch_*` (the chat uses direct `RichLog.write` via a handler, mirroring `advisor.py`'s dock-mount pattern — the assistant surfaces are append-only logs, not reactive-replace like snapshot/events; consistent with `advisor._on_recommendation`); `@REGISTRY.register`+binder (`register_chat` like `register_advisor`); `Container`+`border_title`+widget+`theme.tcss` `$`-tokens; keyboard-first `Input`; pure Rich renderers. No divergent style.
2. **Placeholder scan:** every step has runnable code or an exact command; the `...  # UNCHANGED` markers point at existing code to preserve.
3. **Type consistency:** `_publish_operator_message` allowlist ↔ the daemon receive loop; `next_operator_message -> (kind, msg)` ↔ `on_input_submitted`; `is_confirm_question`/`_milestone_id` ↔ the Task-6 proposal's `context_refs=["milestone:<id>"]`; the `source` tag flows daemon→bus→TUI-handlers. `send_operator_message` uses the worker's `_ws_send` handle.
