# TUI Chat Hydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the TUI connects, the Assistant Chat pane shows the recent conversation instead of starting blank.

**Architecture:** The daemon already replays one frame on connect (`working_memory_snapshot`). This adds a second, `chat_history`, built from the transcript repo the lifespan leaves on `app.state`, and a TUI handler that renders each turn with a new pure formatter beside the six already in `chat.py`. No new facade, widget, pane, or state beyond a one-shot hydration flag.

**Tech Stack:** FastAPI WebSocket (`/v1/tui`), Textual 8.2.7, Rich `Text`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-27-tui-chat-hydration-design.md` · **Issue:** #41

> **This plan was audited against source before implementation.** Findings folded in: the reconnect duplication guard (Task 1 Step 3), the bounded WS receive helper (Task 2 Step 1), the two `test_tui_ws_broadcast.py` tests this change breaks (Task 2 Step 3), `asdict` inside the `try`, the `_HISTORY_LINES` naming, and four tests that did not discriminate. Do not undo these — each is there because it failed a check.

## Global Constraints

- **Textual 8.2.7 has NO `Static.renderable`.** Read a `Static` with `str(widget.content)` (`tests/unit/tui_textual/test_app_reactive.py:22`) and `RichLog` lines with `Strip.text` (`tests/unit/tui_textual/test_chat.py:390`). Do not invent an accessor.
- **No separator, no dimming, no truncation marker.** History renders like live; the thread simply starts. Decided against a first draft — do not re-add.
- **A historical advisory's `opsec` line is UNCOLOURED.** The recorder flattens an advisory to text, so `opsec_risk` is not on disk. Do not re-parse it out of the text, and do not add a field to `ChatTurn`.
- **Hydration is ONE-SHOT per pane.** The TUI auto-reconnects (`ws_worker.py:57`) and every reconnect re-sends the frame; the chat log is append-only, so re-rendering would duplicate the whole thread.
- **The window caps LINES, not turns.** `repo.tail(max_lines=50)` returns the parseable turns among the last 50 lines, so a transcript with a malformed line yields **fewer than 50**. The constant is `_HISTORY_LINES` for that reason.
- **`api` must NOT import `infrastructure`** (`importlinter.cfg:35-43`, a `forbidden` contract). Reach the repo through `app.state`, exactly as `_build_initial_snapshot` does (`tui_ws.py:135-139`).
- **Never block or crash the connect.** A missing repo, a failing read, or a repo returning non-dataclasses must all yield `turns: []` with the socket intact.
- Five CI gates + `pytest adapters/` must pass: `uv run --no-sync ruff check .`, `ruff format --check .`, `mypy redrobin/domain redrobin/application`, `lint-imports --config importlinter.cfg`, `pytest tests/ -v --tb=short`, `pytest adapters/ -v --tb=short`.
- Baseline before this plan: **718 passed / 4 skipped** (`tests/`, 722 collected), **3 passed** (`adapters/`).
- Atomic conventional commits. **No AI co-author trailer.** **Plain ASCII commit messages** — an em-dash or `::` makes `git commit` fail with EPERM here. Use `git commit --no-verify` (the pre-commit hook spawns `uv run` and can fail).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/tui/textual_ui/chat.py` | Chat pane, its formatters and frame handlers | `format_history_turn` + `_on_chat_history` + `_hydrated` flag + one registration |
| `redrobin/api/tui_ws.py` | The `/v1/tui` socket and its connect-time frames | `_build_chat_history` + one `_send` |
| `tests/unit/tui_textual/test_chat.py` | Formatter + handler unit tests | Add 11 tests |
| `tests/integration/test_tui_ws.py` | Connect-time frame contract | Add a bounded receive helper + 5 tests |
| `tests/integration/test_tui_ws_broadcast.py` | Bus-forwarding over the socket | **Fix 2 existing tests** that consume only one connect frame |

---

### Task 1: TUI renders a `chat_history` frame, once

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py`
- Test: `tests/unit/tui_textual/test_chat.py`

**Interfaces:**
- Consumes: `ChatPane.append_line(Text)`, `_chat(app)`, `Registry.register`, `_ACCENT`, `_MUTED`, `_DANGER`, `_RISK_COLORS`, and the test helper `_span_styles` — all existing.
- Produces: `format_history_turn(turn: dict[str, Any]) -> Text` and `_on_chat_history(app, payload) -> None`, registered for frame type `chat_history`, plus `ChatPane._hydrated: bool`. Task 2 produces the frame this consumes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tui_textual/test_chat.py`. The file already imports `pytest`, `RichLog`, `Registry`, `register_chat` and defines `_ChatHarness` and `_span_styles` — nothing new to import.

```python
def test_format_history_turn_labels_the_operator():
    from redrobin.tui.textual_ui.chat import format_history_turn

    s = format_history_turn(
        {"ts": "t", "role": "operator", "kind": "question", "text": "what is my path to DA?"}
    ).plain
    assert s.startswith("You")
    assert "what is my path to DA?" in s
    assert "·" not in s  # a plain question carries no provenance tag


def test_format_history_turn_labels_the_assistant():
    from redrobin.tui.textual_ui.chat import format_history_turn

    s = format_history_turn(
        {"ts": "t", "role": "assistant", "kind": "answer", "text": "you hold Administrator"}
    ).plain
    assert s.startswith("Red Robin")
    assert "you hold Administrator" in s
    assert "·" not in s


def test_format_history_turn_tags_an_advisory_and_starts_its_body_on_a_new_line():
    from redrobin.tui.textual_ui.chat import format_history_turn

    s = format_history_turn(
        {
            "ts": "t",
            "role": "assistant",
            "kind": "advisory",
            "text": "Enumerate 10.100.0.0/24\nwhy    you hold Administrator\nopsec  WARN",
        }
    ).plain
    assert s.startswith("Red Robin · advisory")
    # the multi-line body must not be jammed onto the label line
    assert s.splitlines()[0].strip() == "Red Robin · advisory"
    assert "opsec  WARN" in s


def test_format_history_turn_does_not_colour_a_historical_opsec_value():
    """Pins the spec decision, so nobody later 'restores' the colour by re-parsing.

    The recorder flattens an advisory to text, so ``opsec_risk`` is not on disk. Adding
    the colour back would mean scraping the stored string, or adding a field only
    post-ship rows could carry.

    KNOWN HOLE: ``_ACCENT`` is excluded because it is also the label's style, and it
    happens to be ``_RISK_COLORS["ESCALATE"]`` — so an implementation that coloured an
    ESCALATE line specifically would slip past. The realistic failure mode is re-parsing
    WARN/DANGER, which this does catch.
    """
    from redrobin.tui.textual_ui.chat import _ACCENT, _DANGER, _RISK_COLORS, format_history_turn

    t = format_history_turn(
        {
            "ts": "t",
            "role": "assistant",
            "kind": "advisory",
            "text": "Dump LSASS\nwhy    you hold SeDebug\nopsec  DANGER",
        }
    )
    styles = _span_styles(t)
    assert "DANGER" in t.plain  # the value is still legible
    assert _DANGER not in styles  # ...just not highlighted
    assert all(colour not in styles for colour in _RISK_COLORS.values() if colour != _ACCENT)


def test_format_history_turn_hyphenates_heads_up():
    from redrobin.tui.textual_ui.chat import format_history_turn

    s = format_history_turn(
        {"ts": "t", "role": "assistant", "kind": "heads_up", "text": "beacons look stale"}
    ).plain
    assert "heads-up" in s
    assert "heads_up" not in s  # the stored kind is snake_case; the label is not


def test_format_history_turn_tags_a_confirm_on_one_line():
    from redrobin.tui.textual_ui.chat import format_history_turn

    s = format_history_turn(
        {"ts": "t", "role": "assistant", "kind": "confirm", "text": "Mark obj-1 COMPLETE?"}
    ).plain
    assert s.startswith("Red Robin · confirm")
    assert s.count("\n") == 0  # a confirm is one line, unlike an advisory body


@pytest.mark.asyncio
async def test_on_chat_history_appends_one_line_per_turn_in_order():
    from redrobin.tui.textual_ui.chat import _on_chat_history

    async with _ChatHarness().run_test() as pilot:
        _on_chat_history(
            pilot.app,
            {
                "turns": [
                    {"ts": "t1", "role": "operator", "kind": "question", "text": "first"},
                    {"ts": "t2", "role": "assistant", "kind": "answer", "text": "second"},
                    {"ts": "t3", "role": "operator", "kind": "question", "text": "third"},
                ]
            },
        )
        rendered = [s.text for s in pilot.app.query_one("#chat-log", RichLog).lines]
        # exactly one line per turn: no separator, no marker (spec decision)
        assert len(rendered) == 3
        assert "first" in rendered[0]
        assert "second" in rendered[1]
        assert "third" in rendered[2]  # order is the daemon's, not re-sorted


@pytest.mark.asyncio
async def test_on_chat_history_renders_a_multi_line_advisory_body():
    from redrobin.tui.textual_ui.chat import _on_chat_history

    async with _ChatHarness().run_test() as pilot:
        _on_chat_history(
            pilot.app,
            {
                "turns": [
                    {
                        "ts": "t1",
                        "role": "assistant",
                        "kind": "advisory",
                        "text": "Enumerate the subnet\nwhy    you hold Administrator\nopsec  WARN",
                    }
                ]
            },
        )
        rendered = [s.text for s in pilot.app.query_one("#chat-log", RichLog).lines]
        joined = "\n".join(rendered)
        assert "Red Robin · advisory" in joined
        assert "opsec  WARN" in joined  # the body survives the trip through RichLog


@pytest.mark.asyncio
async def test_on_chat_history_tolerates_empty_and_malformed_payloads():
    from redrobin.tui.textual_ui.chat import _on_chat_history

    async with _ChatHarness().run_test() as pilot:
        log = pilot.app.query_one("#chat-log", RichLog)
        _on_chat_history(pilot.app, {"turns": []})
        _on_chat_history(pilot.app, {})  # no turns key: `for turn in None` would raise
        _on_chat_history(pilot.app, {"turns": 42})  # non-iterable: the outer guard is load-bearing
        assert len(log.lines) == 0
        # one bad entry must not drop the good ones around it
        _on_chat_history(
            pilot.app,
            {
                "turns": [
                    {"ts": "t", "role": "operator", "kind": "question", "text": "kept"},
                    "junk",
                ]
            },
        )
        assert len(log.lines) == 1
        assert "kept" in log.lines[0].text


@pytest.mark.asyncio
async def test_on_chat_history_hydrates_only_once_across_reconnects():
    """The TUI auto-reconnects (ws_worker.py:57) and every reconnect re-sends the frame.

    The chat log is append-only, so without a guard a ping timeout or a daemon restart
    re-appends the whole thread below the live conversation.
    """
    from redrobin.tui.textual_ui.chat import _on_chat_history

    payload = {
        "turns": [
            {"ts": "t1", "role": "operator", "kind": "question", "text": "only once"},
            {"ts": "t2", "role": "assistant", "kind": "answer", "text": "indeed"},
        ]
    }
    async with _ChatHarness().run_test() as pilot:
        log = pilot.app.query_one("#chat-log", RichLog)
        _on_chat_history(pilot.app, payload)
        assert len(log.lines) == 2
        _on_chat_history(pilot.app, payload)  # the reconnect replay
        assert len(log.lines) == 2  # unguarded -> 4


@pytest.mark.asyncio
async def test_chat_history_is_registered_as_a_frame_type():
    reg = Registry()
    register_chat(reg)
    assert reg.has_handler("chat_history")
    async with _ChatHarness().run_test() as pilot:
        handled = reg.dispatch(
            target=pilot.app,
            frame={
                "type": "chat_history",
                "payload": {
                    "turns": [{"ts": "t", "role": "operator", "kind": "question", "text": "hi"}]
                },
            },
        )
        assert handled is True
        assert len(pilot.app.query_one("#chat-log", RichLog).lines) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v -k history`
Expected: FAIL — `ImportError: cannot import name 'format_history_turn'` (imports are function-local, so collection succeeds), and the registration test fails on `has_handler("chat_history")`.

- [ ] **Step 3: Implement the formatter, the handler and the one-shot guard**

In `redrobin/tui/textual_ui/chat.py`, add beside the existing `from redrobin.domain.assistant import ...`:

```python
from redrobin.domain.chat import VOLUNTEERED_KINDS
```

Add the constant after `_RISK_COLORS`:

```python
# A volunteered turn (advisory / heads-up) and a confirm carry their kind in the label:
# the speaker alone does not say whether Red Robin was answering or speaking up. A plain
# question or answer needs no tag. This WIDENS the LLM slice's rule in
# application/chat/history.py, which tags only VOLUNTEERED_KINDS — a confirm needs
# distinguishing from an answer on screen in a way the model does not need in prose.
_TAGGED_KINDS = VOLUNTEERED_KINDS | {"confirm"}
```

Add `self._hydrated = False` to `ChatPane.__init__`, beside the other instance attributes:

```python
        self._hydrated = False  # chat_history is replayed on every reconnect; render it once
```

Add the formatter beside the other `format_*` functions (keeping `__all__` alphabetical, it goes between `format_confirm_bar` and `format_operator_echo`):

```python
def format_history_turn(turn: dict[str, Any]) -> Text:
    """One hydrated transcript turn as a chat line (spec 2026-07-27).

    Deliberately the SAME shape as the live renderers — no dimming, no separator, no
    "earlier" marker. The thread just starts, the way a chat client resumes.

    One difference from live, and it is intended: a historical advisory's ``opsec``
    value is not coloured. ``ChatRecorder`` flattens an advisory to text, so the
    structured ``opsec_risk`` that drives ``_RISK_COLORS`` is not on disk. Colouring it
    would mean either re-parsing the stored text (fragile) or adding a field that only
    post-ship rows could carry (a history where some advisories are coloured and others
    are not, which reads as a bug).
    """
    label = "You" if turn.get("role") == "operator" else "Red Robin"
    kind = str(turn.get("kind") or "")
    t = Text()
    t.append(label, style=f"bold {_ACCENT}")
    if kind in _TAGGED_KINDS:
        t.append(f" · {kind.replace('_', '-')}", style=_MUTED)
    # A volunteered body is the multi-line action/why/opsec/conf block, so it begins on
    # its own line exactly as the live advisory does. Dialogue is one line after the label.
    t.append("\n" if kind in VOLUNTEERED_KINDS else "  ")
    t.append(str(turn.get("text") or ""))
    return t
```

Add the handler beside `_on_recommendation` / `_on_answer`:

```python
def _on_chat_history(app: Any, payload: dict[str, Any]) -> None:
    """Hydrate the pane from the transcript the daemon sends on connect.

    ONE-SHOT. ``ws_worker`` auto-reconnects (``async for ws in connect(...)``), and every
    reconnect re-runs the endpoint from the top and re-sends this frame. The working-memory
    frame is idempotent because it REPLACES ``app.snapshot``; this log is append-only, so
    without the guard a ping timeout would re-append the whole thread below the live
    conversation.

    Known limitation of one-shot: turns recorded while the socket was down are not
    rendered on reconnect. They are on disk and in the assistant's own context, and the
    operator sees them by restarting the TUI. Rendering only turns newer than the last
    hydrated one was considered and rejected: a live-rendered turn carries no ``ts`` in
    the TUI, so the watermark could not be advanced for it, and the next reconnect would
    duplicate everything rendered live in between.

    Order is the daemon's (file order, oldest first) — never re-sorted. A malformed entry
    is skipped rather than losing the whole thread: rendering nine of ten turns beats none.
    """
    pane = _chat(app)
    if pane is None or pane._hydrated:
        return
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return
    pane._hydrated = True
    for turn in turns:
        if isinstance(turn, dict):
            pane.append_line(format_history_turn(turn))
```

Register it in `register_chat`:

```python
    registry.register("chat_history")(_on_chat_history)
```

Add `"format_history_turn"` to `__all__`, alphabetically.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Run the full gates**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
```
Expected: all green; `tests/` at **729 passed / 4 skipped** (718 + 11).

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/chat.py tests/unit/tui_textual/test_chat.py
git commit --no-verify -m "feat(tui): render a chat_history frame into the chat pane" -m "The pane starts blank on connect while Working Memory and Sessions hydrate. Add the renderer and handler for a chat_history frame so a reopened TUI shows the recent conversation. History renders like live, with no separator or dimming. Hydration is one-shot per pane because the TUI auto-reconnects and the log is append-only, so replaying the frame would duplicate the thread. A historical advisory's opsec line is uncoloured because the recorder flattens an advisory to text and the structured value is not on disk."
```

---

### Task 2: Daemon sends `chat_history` on connect

**Files:**
- Modify: `redrobin/api/tui_ws.py`
- Test: `tests/integration/test_tui_ws.py`
- Fix: `tests/integration/test_tui_ws_broadcast.py`

**Interfaces:**
- Consumes: `app.state.chat_history_repo` (set at `lifespan_helpers.py:428`, only when `settings.assistant_enabled` **and** a working-memory repo exists), whose `tail(max_lines: int) -> list[ChatTurn]` returns the parseable turns among the last N raw lines, oldest first.
- Produces: `{"type": "chat_history", "payload": {"turns": [...]}}`, each turn the `asdict` of a `ChatTurn` (`ts`, `role`, `kind`, `text`, `correlation_id`). Task 1 consumes it.

- [ ] **Step 1: Write the failing tests**

First add this helper near the top of `tests/integration/test_tui_ws.py`, after the fixtures. **It is required**: `TestClient`'s WS receive blocks on a memory stream with no timeout, and `/v1/tui` parks in its outbound loop forever, so a frame that never arrives would HANG pytest instead of failing it.

```python
import threading
from typing import Any


def _receive_json(ws, timeout: float = 5.0) -> Any:
    """A BOUNDED ``ws.receive_json()``.

    starlette's TestClient receives through a portal with no timeout, and after the app
    returns its ``_run`` sleeps forever, so the stream never closes either. Without this,
    a missing frame wedges the whole pytest session rather than failing a test — which
    would make the RED step of this task unrunnable. ``pytest-timeout`` is not installed.
    """
    box: dict[str, Any] = {}

    def _pull() -> None:
        try:
            box["frame"] = ws.receive_json()
        except Exception as exc:  # the portal can be torn down under us on timeout
            box["error"] = exc

    thread = threading.Thread(target=_pull, daemon=True)
    thread.start()
    thread.join(timeout)
    if "frame" not in box:
        raise AssertionError(f"no frame within {timeout}s (last error: {box.get('error')!r})")
    return box["frame"]
```

Then append the tests:

```python
def test_ws_sends_chat_history_after_the_snapshot(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    # the assistant is disabled in this fixture, so the lifespan sets no repo: the empty
    # case is the COMMON path, not an edge case
    assert not hasattr(client.app.state, "chat_history_repo")
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = _receive_json(ws)
        second = _receive_json(ws)
    assert first["type"] == "working_memory_snapshot"
    assert second["type"] == "chat_history"
    assert second["payload"]["turns"] == []


def test_ws_chat_history_carries_the_full_turn_shape(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(
        ChatTurn(ts="2026-07-27T00:00:00+00:00", role="operator", kind="question", text="hello")
    )
    repo.append(
        ChatTurn(
            ts="2026-07-27T00:00:01+00:00",
            role="assistant",
            kind="advisory",
            text="enumerate the subnet",
            correlation_id="B-1:whoami",
        )
    )
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)  # working_memory_snapshot
        frame = _receive_json(ws)
    turns = frame["payload"]["turns"]
    assert [t["text"] for t in turns] == ["hello", "enumerate the subnet"]  # oldest first
    # the frame carries the whole ChatTurn, not a subset (spec: the frame contract)
    assert set(turns[0]) == {"ts", "role", "kind", "text", "correlation_id"}
    assert turns[0]["correlation_id"] is None
    assert turns[1]["correlation_id"] == "B-1:whoami"


def test_ws_chat_history_reads_one_bounded_tail_of_the_most_recent_turns(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    inner = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for i in range(60):
        inner.append(
            ChatTurn(
                ts=f"2026-07-27T00:00:{i:02d}+00:00",
                role="operator",
                kind="question",
                text=f"turn-{i}",
            )
        )

    class _RecordingRepo:
        """Pins that the builder asks the STORE for a bounded window.

        Without this, a `tail()[-50:]` implementation would pass the assertions below
        while reading the whole 2000-line file on every connect.
        """

        def __init__(self, delegate):
            self._delegate = delegate
            self.max_lines_seen = None

        def tail(self, max_lines=None):
            self.max_lines_seen = max_lines
            return self._delegate.tail(max_lines=max_lines)

    repo = _RecordingRepo(inner)
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert repo.max_lines_seen == 50  # one bounded read, not a full read then a slice
    texts = [t["text"] for t in frame["payload"]["turns"]]
    assert len(texts) == 50
    assert texts[0] == "turn-10"  # the MOST RECENT 50...
    assert texts[-1] == "turn-59"  # ...still oldest-first


def test_ws_chat_history_survives_a_failing_repo(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _BoomRepo:
        def tail(self, max_lines=None):
            raise OSError("transcript unreadable")

    client.app.state.chat_history_repo = _BoomRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []


def test_ws_chat_history_survives_a_repo_returning_non_turns(client):
    """The connect must not crash on a repo that returns something unexpected.

    ``asdict`` raises TypeError on a plain dict, and the endpoint's outer try catches
    only WebSocketDisconnect, so an unguarded mapping would close the socket with 1011
    and drop the TUI into its reconnect loop.
    """
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _WrongShapeRepo:
        def tail(self, max_lines=None):
            return [{"ts": "t", "role": "operator", "kind": "question", "text": "not a turn"}]

    client.app.state.chat_history_repo = _WrongShapeRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        _receive_json(ws)
        frame = _receive_json(ws)
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/integration/test_tui_ws.py -v -k chat_history`
Expected: FAIL with `AssertionError: no frame within 5.0s` — only `working_memory_snapshot` is sent today, and the bounded helper turns the missing second frame into a failure instead of a hang.

- [ ] **Step 3: Implement the builder, send it, and fix the two broadcast tests**

In `redrobin/api/tui_ws.py`, add to the imports:

```python
from dataclasses import asdict
```

Add the constant beside `_TUI_ALLOWED`:

```python
# How many transcript LINES the chat pane hydrates from on connect. Named for lines, not
# turns, because ``tail`` caps lines and drops unparseable ones without backfilling, so a
# transcript with a malformed line yields fewer. A module constant, not a Setting (YAGNI,
# matching MAX_TAIL_LINES) — one bounded read, never a full read sliced afterwards.
_HISTORY_LINES = 50
```

Add the builder beside `_build_initial_snapshot`:

```python
async def _build_chat_history(app) -> dict:
    """Build the connect-time ``chat_history`` payload from the transcript repo.

    Mirrors ``_build_initial_snapshot``: reads the repo the lifespan left on
    ``app.state``, so this layer never imports infrastructure (import-linter contract 4).

    The repo is ABSENT whenever the assistant is disabled — ``lifespan_helpers`` gates it
    on ``assistant_enabled``, which is off by default — so the empty result is the common
    path, not a defensive edge. Both the read AND the mapping sit inside the guard: the
    endpoint's outer try catches only WebSocketDisconnect, so anything raised here would
    close the socket and drop the TUI into its reconnect loop. A chat that cannot hydrate
    must never cost the operator their Working Memory and Sessions panes.
    """
    repo = getattr(app.state, "chat_history_repo", None)
    if repo is None:
        return {"turns": []}
    try:
        turns = repo.tail(max_lines=_HISTORY_LINES)
        return {"turns": [asdict(turn) for turn in turns]}
    except Exception:
        logger.exception("tui_ws: could not read the chat transcript; hydrating an empty thread")
        return {"turns": []}
```

In `tui_ws`, immediately after the `working_memory_snapshot` send block:

```python
        # Chat hydration (#41). Sent AFTER the snapshot and BEFORE the live loop, so the
        # pane holds the thread before any new event lands on it.
        #
        # We subscribe to the bus above and only read the transcript here. Measured on
        # this code today, that window contains no guaranteed yield point (the bus lock is
        # uncontended and send_json did not suspend even on a 900 KB frame), so a turn
        # cannot currently slip in between and render twice. The order is kept anyway,
        # deliberately: if a yield point ever appears here, this way costs a visible,
        # harmless duplicate, whereas reading before subscribing would SILENTLY LOSE the
        # turn. Do not reverse it.
        if not await _send(
            websocket,
            {"type": "chat_history", "payload": await _build_chat_history(websocket.app)},
        ):
            return  # client already gone — the outer finally unsubscribes
```

Update the module docstring's first paragraph:

```python
"""WebSocket endpoint /v1/tui — pushes EventBus events to the TUI client.

On connect the client receives a ``working_memory_snapshot`` frame and a
``chat_history`` frame, then every event published on ``app.state.event_bus`` is
forwarded over the socket. The per-client queue is unsubscribed on disconnect (or
sentinel ``{_type: _close}``).
"""
```

**Then fix `tests/integration/test_tui_ws_broadcast.py`.** Two tests there discard exactly one connect frame and assert the next is their published bus event; with a second connect frame they now receive `chat_history` and fail. Consume both connect frames in each:

- `test_ws_forwards_published_event` — the single `ws.receive_json()  # discard snapshot` becomes two receives (snapshot, then chat_history), commented as *"two connect frames now: the snapshot and the chat history"*.
- `test_two_ws_clients_both_receive` — same, for each of the two sockets.

Do not change what these tests assert; only how many connect frames they skip.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/integration/test_tui_ws.py tests/integration/test_tui_ws_broadcast.py -v
```
Expected: PASS, including the pre-existing auth and snapshot tests.

- [ ] **Step 5: Run the full gates**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
uv run --no-sync pytest adapters/ -q
```
Expected: all green; `tests/` at **734 passed / 4 skipped** (729 + 5), `adapters/` at 3 passed.

**If `lint-imports` fails**, the builder is importing infrastructure — it must reach the repo through `app.state` only.
**If two tests in `test_tui_ws_broadcast.py` fail**, the connect-frame fix above was not applied.

- [ ] **Step 6: Commit**

```bash
git add redrobin/api/tui_ws.py tests/integration/test_tui_ws.py tests/integration/test_tui_ws_broadcast.py
git commit --no-verify -m "feat(api): send a chat_history frame when the TUI connects" -m "The connect path replayed working memory but not the conversation, so a reopened TUI showed a blank chat pane while the transcript sat on disk and in the assistant's own context. Send the last 50 lines of transcript as a chat_history frame, read through app.state so this layer does not import infrastructure. A missing repo (the assistant is disabled by default), a failing read, or a repo returning non-dataclasses all yield an empty thread rather than a failed connect. Two tests in test_tui_ws_broadcast.py consumed exactly one connect frame and now consume both."
```

---

## Verification

Deterministic behaviour is covered by the tests above. The **visible result is not** — `.claude/rules/development-workflow.md` lists the Textual TUI as live surface, so the operator acceptance in the spec's DoD §3 is the real gate:

1. Start the daemon with `REDROBIN_ASSISTANT_ENABLED=1` against `vantage`.
2. Open the TUI, ask the assistant something, get an answer, quit with `q`.
3. **Reopen the TUI — the exchange is there**, reads as a conversation, no duplicated or out-of-order lines.
4. Send a new message: it appends below the hydrated thread in the same style.
5. **Restart the daemon while the TUI stays open.** The TUI reconnects on its own; the thread must NOT be re-appended. (This is the reconnect path, which a quit-and-reopen smoke does not exercise.)
6. The scroll position on open shows the most recent turns, not the top of the history.
