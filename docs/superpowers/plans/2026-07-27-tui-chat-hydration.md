# TUI Chat Hydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the TUI connects, the Assistant Chat pane shows the recent conversation instead of starting blank.

**Architecture:** The daemon already replays one frame on connect (`working_memory_snapshot`). This adds a second, `chat_history`, built from the transcript repo the lifespan leaves on `app.state`, and a TUI handler that renders each turn with a new pure formatter beside the six already in `chat.py`. No new facade, widget, pane, or state.

**Tech Stack:** FastAPI WebSocket (`/v1/tui`), Textual 8.2.7, Rich `Text`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-27-tui-chat-hydration-design.md` · **Issue:** #41

## Global Constraints

- **Textual 8.2.7 has NO `Static.renderable`.** Read rendered content with `str(widget.content)` for a `Static` and `Strip.text` for `RichLog` lines (`tests/unit/tui_textual/test_app_reactive.py:22`). Do not invent an accessor.
- **No separator, no dimming, no truncation marker.** History renders like live; the thread simply starts. This was decided against a first draft and must not be re-added.
- **A historical advisory's `opsec` line is UNCOLOURED.** The recorder flattens an advisory to text, so `opsec_risk` is not on disk. Do not re-parse it out of the text, and do not add a field to `ChatTurn`.
- **Window is the last 50 turns**, via a single `repo.tail(max_lines=50)`. A module constant, not a `Setting`.
- **`api` must NOT import `infrastructure`** (import-linter contract 4). Reach the repo through `app.state`, exactly as `_build_initial_snapshot` does.
- **Never block the connect.** A missing or failing repo yields `turns: []`; the operator still gets Working Memory and Sessions.
- Five CI gates + `pytest adapters/` must pass: `uv run --no-sync ruff check .`, `ruff format --check .`, `mypy redrobin/domain redrobin/application`, `lint-imports --config importlinter.cfg`, `pytest tests/ -v --tb=short`, `pytest adapters/ -v --tb=short`.
- Baseline before this plan: **718 passed / 4 skipped** (`tests/`), **3 passed** (`adapters/`).
- Atomic conventional commits. **No AI co-author trailer.** **Plain ASCII commit messages** — an em-dash or `::` makes `git commit` fail with EPERM in this environment. Use `git commit --no-verify` (the pre-commit hook spawns `uv run` and can fail).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/tui/textual_ui/chat.py` | Chat pane, its formatters and frame handlers | Add `format_history_turn` + `_on_chat_history` + one registration |
| `redrobin/api/tui_ws.py` | The `/v1/tui` socket and its connect-time frames | Add `_build_chat_history` + one `_send` |
| `tests/unit/tui_textual/test_chat.py` | Formatter + handler unit tests | Add 7 tests |
| `tests/integration/test_tui_ws.py` | Connect-time frame contract | Add 4 tests |

---

### Task 1: TUI renders a `chat_history` frame

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py`
- Test: `tests/unit/tui_textual/test_chat.py`

**Interfaces:**
- Consumes: `ChatPane.append_line(Text)`, `_chat(app)`, `Registry.register` — all existing in `chat.py`.
- Produces: `format_history_turn(turn: dict[str, Any]) -> Text` and `_on_chat_history(app, payload) -> None`, registered for frame type `chat_history`. Task 2 produces the frame this consumes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tui_textual/test_chat.py`:

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

    t = format_history_turn(
        {
            "ts": "t",
            "role": "assistant",
            "kind": "advisory",
            "text": "Enumerate 10.100.0.0/24\nwhy    you hold Administrator\nopsec  WARN",
        }
    )
    s = t.plain
    assert s.startswith("Red Robin · advisory")
    # the multi-line body must not be jammed onto the label line
    assert s.splitlines()[0].strip() == "Red Robin · advisory"
    assert "opsec  WARN" in s


def test_format_history_turn_does_not_colour_a_historical_opsec_value():
    """Pins the spec decision, so nobody later 'restores' the colour by re-parsing.

    The recorder flattens an advisory to text, so ``opsec_risk`` is not on disk. Adding
    the colour back would mean scraping the stored string or adding a field only
    post-ship rows could carry.
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
    styles = " ".join(str(span.style) for span in t.spans)
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
        assert len(rendered) == 3
        assert "first" in rendered[0]
        assert "second" in rendered[1]
        assert "third" in rendered[2]  # order is the daemon's, not re-sorted


@pytest.mark.asyncio
async def test_on_chat_history_tolerates_empty_and_malformed_payloads():
    from redrobin.tui.textual_ui.chat import _on_chat_history

    async with _ChatHarness().run_test() as pilot:
        log = pilot.app.query_one("#chat-log", RichLog)
        _on_chat_history(pilot.app, {"turns": []})
        _on_chat_history(pilot.app, {})  # no turns key at all
        _on_chat_history(pilot.app, {"turns": "not-a-list"})
        assert len(log.lines) == 0
        # one bad entry must not drop the good ones around it
        _on_chat_history(
            pilot.app,
            {"turns": [{"ts": "t", "role": "operator", "kind": "question", "text": "kept"}, "junk"]},
        )
        assert len(log.lines) == 1


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
Expected: FAIL — `ImportError: cannot import name 'format_history_turn'`, and the registration test fails on `has_handler("chat_history")`.

- [ ] **Step 3: Implement the formatter and handler**

In `redrobin/tui/textual_ui/chat.py`, add the domain import beside the existing `from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX`:

```python
from redrobin.domain.chat import VOLUNTEERED_KINDS
```

Add the constant beside the other module constants (after `_RISK_COLORS`):

```python
# A volunteered turn (advisory / heads-up) and a confirm carry their kind in the label:
# the speaker alone does not say whether Red Robin was answering or speaking up. A plain
# question or answer needs no tag — who said it is the whole story.
_TAGGED_KINDS = VOLUNTEERED_KINDS | {"confirm"}
```

Add the formatter beside the other `format_*` functions:

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

    Order is the daemon's (file order, oldest first) — the handler never re-sorts. A
    malformed entry is skipped rather than losing the whole thread: a transcript is
    append-only operator history, so rendering nine of ten turns beats rendering none.
    """
    pane = _chat(app)
    if pane is None:
        return
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return
    for turn in turns:
        if isinstance(turn, dict):
            pane.append_line(format_history_turn(turn))
```

Register it in `register_chat`:

```python
    registry.register("chat_history")(_on_chat_history)
```

Add `"format_history_turn"` to the module's `__all__`.

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
Expected: all green; `tests/` at **727 passed / 4 skipped** (718 + 9).

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/chat.py tests/unit/tui_textual/test_chat.py
git commit --no-verify -m "feat(tui): render a chat_history frame into the chat pane" -m "The pane starts blank on connect while Working Memory and Sessions hydrate. Add the renderer and handler for a chat_history frame so a reopened TUI shows the recent conversation. History renders like live, with no separator or dimming. A historical advisory's opsec line is uncoloured because the recorder flattens an advisory to text and the structured value is not on disk."
```

---

### Task 2: Daemon sends `chat_history` on connect

**Files:**
- Modify: `redrobin/api/tui_ws.py`
- Test: `tests/integration/test_tui_ws.py`

**Interfaces:**
- Consumes: `app.state.chat_history_repo` (set by `lifespan_helpers.py:428`, **only when the assistant is enabled**), whose `tail(max_lines: int) -> list[ChatTurn]` returns parseable turns among the last N raw lines, oldest first.
- Produces: a frame `{"type": "chat_history", "payload": {"turns": [...]}}`, each turn the `asdict` of a `ChatTurn` (`ts`, `role`, `kind`, `text`, `correlation_id`). Task 1 consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_tui_ws.py`:

```python
def test_ws_sends_chat_history_after_the_snapshot(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["type"] == "working_memory_snapshot"
    assert second["type"] == "chat_history"
    # no repo on app.state here: the assistant is disabled by default, which is the
    # COMMON path, not an edge case
    assert second["payload"]["turns"] == []


def test_ws_chat_history_carries_the_transcript(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    repo.append(
        ChatTurn(ts="2026-07-27T00:00:00+00:00", role="operator", kind="question", text="hello")
    )
    repo.append(
        ChatTurn(
            ts="2026-07-27T00:00:01+00:00", role="assistant", kind="answer", text="standing by"
        )
    )
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # working_memory_snapshot
        frame = ws.receive_json()
    turns = frame["payload"]["turns"]
    assert [t["text"] for t in turns] == ["hello", "standing by"]  # oldest first
    assert turns[0]["role"] == "operator" and turns[0]["kind"] == "question"


def test_ws_chat_history_is_capped_at_the_most_recent_turns(client, tmp_path):
    from redrobin.application.chat import ChatHistoryRepo
    from redrobin.domain.chat import ChatTurn
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    repo = ChatHistoryRepo(tmp_path / "chat_history.jsonl")
    for i in range(60):
        repo.append(
            ChatTurn(
                ts=f"2026-07-27T00:00:{i:02d}+00:00",
                role="operator",
                kind="question",
                text=f"turn-{i}",
            )
        )
    client.app.state.chat_history_repo = repo

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()
        frame = ws.receive_json()
    texts = [t["text"] for t in frame["payload"]["turns"]]
    assert len(texts) == 50
    # the MOST RECENT 50, still oldest-first
    assert texts[0] == "turn-10"
    assert texts[-1] == "turn-59"


def test_ws_chat_history_survives_a_failing_repo(client):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    class _BoomRepo:
        def tail(self, max_lines=None):
            raise OSError("transcript unreadable")

    client.app.state.chat_history_repo = _BoomRepo()
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()
        frame = ws.receive_json()
    # the connect still succeeds and the other panes still hydrate
    assert frame["type"] == "chat_history"
    assert frame["payload"]["turns"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/integration/test_tui_ws.py -v -k chat_history`
Expected: FAIL — the second frame is a bus event or the socket blocks, because no `chat_history` frame is sent.

- [ ] **Step 3: Implement the builder and send it**

In `redrobin/api/tui_ws.py`, add to the imports:

```python
from dataclasses import asdict
```

Add the constant beside `_TUI_ALLOWED`:

```python
# How many transcript turns the chat pane hydrates with on connect. A module constant,
# not a Setting (YAGNI, matching MAX_TAIL_LINES) — read in ONE tail() call rather than a
# full read sliced afterwards, so the connect path stays cheap.
_HISTORY_TURNS = 50
```

Add the builder beside `_build_initial_snapshot`:

```python
async def _build_chat_history(app) -> dict:
    """Build the connect-time ``chat_history`` payload from the transcript repo.

    Mirrors ``_build_initial_snapshot``: reads the repo the lifespan left on
    ``app.state``, so this layer never imports infrastructure (import-linter contract 4).

    The repo is ABSENT whenever the assistant is disabled — ``lifespan_helpers`` gates it
    on ``assistant_enabled``, which is off by default — so the empty result is the common
    path, not a defensive edge. A read failure degrades the same way: a chat that cannot
    hydrate must never cost the operator their Working Memory and Sessions panes.
    """
    repo = getattr(app.state, "chat_history_repo", None)
    if repo is None:
        return {"turns": []}
    try:
        turns = repo.tail(max_lines=_HISTORY_TURNS)
    except Exception:
        logger.exception("tui_ws: could not read the chat transcript; hydrating an empty thread")
        return {"turns": []}
    return {"turns": [asdict(turn) for turn in turns]}
```

In `tui_ws`, immediately after the `working_memory_snapshot` send block, add:

```python
        # Chat hydration (#41). Sent AFTER the snapshot and BEFORE the live loop, so the
        # pane holds the thread before any new event lands on it.
        #
        # We subscribed to the bus above and only read the transcript now, so a turn
        # written in that window arrives BOTH here and as a live event, rendering twice.
        # That is deliberate: reversing the order would trade a visible, harmless
        # duplicate for a SILENT LOSS of a turn, and for a chat display duplicating beats
        # losing. Do not "fix" it by reading before subscribing.
        if not await _send(
            websocket,
            {"type": "chat_history", "payload": await _build_chat_history(websocket.app)},
        ):
            return  # client already gone — the outer finally unsubscribes
```

Update the module docstring's first paragraph to name both connect-time frames:

```python
"""WebSocket endpoint /v1/tui — pushes EventBus events to the TUI client.

On connect the client receives a ``working_memory_snapshot`` frame and a
``chat_history`` frame, then every event published on ``app.state.event_bus`` is
forwarded over the socket. The per-client queue is unsubscribed on disconnect (or
sentinel ``{_type: _close}``).
"""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/integration/test_tui_ws.py -v`
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
Expected: all green; `tests/` at **731 passed / 4 skipped** (727 + 4), `adapters/` at 3 passed.

**If `lint-imports` fails**, the builder is importing infrastructure — it must reach the repo through `app.state` only, never by importing `ChatHistoryRepo`.

- [ ] **Step 6: Commit**

```bash
git add redrobin/api/tui_ws.py tests/integration/test_tui_ws.py
git commit --no-verify -m "feat(api): send a chat_history frame when the TUI connects" -m "The connect path replayed working memory but not the conversation, so a reopened TUI showed a blank chat pane while the transcript sat on disk and in the assistant's own context. Send the last 50 turns as a chat_history frame, read through app.state so this layer does not import infrastructure. A missing repo (the assistant is disabled by default) or a failing read yields an empty thread rather than a failed connect."
```

---

## Verification

Deterministic behaviour is covered by the tests above. The **visible result is not** — `.claude/rules/development-workflow.md` lists the Textual TUI as live surface, so the operator acceptance in the spec's DoD §3 is the real gate:

1. Start the daemon with `REDROBIN_ASSISTANT_ENABLED=1` against `vantage`.
2. Open the TUI, ask the assistant something, get an answer, quit with `q`.
3. **Reopen the TUI — the exchange is there**, reads as a conversation, no duplicated or out-of-order lines.
4. Send a new message: it appends below the hydrated thread in the same style.
5. The scroll position on open shows the most recent turns, not the top of the history.
