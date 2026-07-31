# Assistant Interactivity Affordances Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the assistant's interactive moments correct AND legible — a close/commit always ASKS (code-enforced), that ask is a prominent non-blocking bar, and the operator can see when the assistant is working.

**Architecture:** Three seams, no new subsystems. (A) enforce-by-construction in the interpretation parser (mirrors the #28 false-commit fix); (B) a reserved-then-revealed `#confirm-bar` inside the ChatPane (the old `#advisor-dock` reveal pattern), driven off the `_pending` list the pane already tracks; (C) a new bus-only `assistant.thinking` event the assistant publishes around each LLM cycle, forwarded by `tui_ws` like `advisor.recommendation` and rendered as a 200ms-delayed, auto-erasing spinner. No change to reasoning, the gate, the enricher, or the `/y /n` false-commit routing.

**Tech Stack:** Python 3.12, asyncio, Textual (TUI), pytest + pytest-asyncio, `uv` for builds.

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from `CLAUDE.md`, `CONTRIBUTING.md`, and the design spec.

- **5 CI gates must pass** (run with `uv run --no-sync` when the daemon is running — it locks the venv):
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run mypy redrobin/domain redrobin/application`
  - `uv run lint-imports --config importlinter.cfg` (run it plainly — do NOT pipe stdout to `/dev/null` on Windows; rich's cp1252 encode raises a spurious `UnicodeEncodeError`)
  - `uv run pytest tests/ -v --tb=short`
- **Boot-gated tests need `REDROBIN_ALLOW_DEGRADED=1`** in the environment (any test that constructs `create_app()` / hits the boot gate). The pure-unit tests in this plan do not, but set it when running the full suite to be safe.
- **TDD**: failing test → verify red → minimal implementation → verify green → commit.
- **Atomic, conventional commits**; one logical change per commit. **NO AI co-author trailer** (no `Co-Authored-By: Claude`).
- **Layered architecture** (import-linter, 4 contracts): `tui`/`api` → `application` → `domain`; `domain` pure; `application` NEVER imports `tui`/`api`; `tui` reaches infra only via an application facade. **`redrobin/tui/*` must not import `redrobin/application/*` internals directly beyond what it already does** — the new `assistant.thinking` handler lives entirely in `tui` and consumes only the frame dict.
- **mypy strict** covers `redrobin/domain` + `redrobin/application` ONLY (Tasks 1–2). `redrobin/tui` is gradual (Tasks 3–4) but must still `ruff` clean.
- **Renderers are pure Rich, unit-testable** — a `format_*` function returns a `rich.text.Text` and touches no widget/app state.
- **Never-crash**: every assistant per-event path and every TUI frame handler swallows-and-logs its own exceptions; a thinking-signal failure must NEVER break a cycle; a pre-mount TUI query must never raise.
- **The confirm stays NON-blocking** — the beacon is never held; do NOT make the confirm modal.
- **The `/y /n` routing + `resolve_confirm` + false-commit safety are UNCHANGED** — existing routing/consume tests stay green untouched.
- **`assistant.thinking` is a bus-only internal event**, NOT a `/v1/events` wire type. Shape: `{"type": "assistant.thinking", "payload": {"state": "start" | "stop"}}`.
- **Textual is pinned at 8.2.7** (verified). `Static.update(renderable)` is correct, but **`Static.renderable` does NOT exist** in 8.2.7 — never assert on it in a test; assert on the state that drives the widget (`_pending`, `_pending_prompts`, `display`, `placeholder`, `_spin_timer`) and on the pure `format_*`/`_thinking_text` renderers. `Widget.set_timer`/`set_interval` return `textual.timer.Timer`; `Widget.border_title`, `App.focused`, `Timer.stop`, and the `border-title-color` CSS property are all valid in 8.2.7.

---

### Task 1: Confirm-on-close enforcement (parser)

**Files:**
- Modify: `redrobin/application/agents/assistant/parser.py` (add `_COMMIT_STATUSES` near line 25; rewrite the tail of `parse_interpretation`, lines 183–194)
- Test: `tests/unit/agents/assistant/test_parser.py` (append new tests)

**Interfaces:**
- Consumes: `parse_interpretation(raw: str) -> Interpretation | None` (existing, signature unchanged); `Interpretation(phase, milestone_id, status, progress_pct, needs_confirm, prompt)` from `redrobin.domain.assistant`.
- Produces: `_COMMIT_STATUSES: set[str]` (module-private); `parse_interpretation` now FORCES `needs_confirm=True` and synthesizes `prompt` for a terminal status on a real `milestone_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/assistant/test_parser.py`:

```python
# ---- interactivity increment: a terminal/commit status ALWAYS asks (enforced in code) ----


def test_terminal_status_forces_confirm_even_if_model_said_false():
    it = parse_interpretation(
        '{"milestone_id": "obj-1", "phase": "privilege_escalation", '
        '"status": "COMPLETE", "needs_confirm": false, "prompt": ""}'
    )
    assert it is not None
    assert it.needs_confirm is True  # forced — a close ALWAYS asks, model said false
    assert it.prompt == "Mark obj-1 COMPLETE?"  # synthesized (model omitted the prompt)


def test_terminal_status_preserves_model_prompt_when_given():
    it = parse_interpretation(
        '{"milestone_id": "obj-2", "status": "DONE", "needs_confirm": false, '
        '"prompt": "Confirm obj-2 fully done?"}'
    )
    assert it is not None and it.needs_confirm is True
    assert it.prompt == "Confirm obj-2 fully done?"  # model prompt preserved, not overwritten


def test_terminal_status_is_case_insensitive():
    it = parse_interpretation('{"milestone_id": "obj-3", "status": "complete"}')
    assert it is not None and it.needs_confirm is True


def test_soft_status_stays_auto_write():
    it = parse_interpretation(
        '{"milestone_id": "obj-4", "status": "in_progress", "progress_pct": 55, '
        '"needs_confirm": false}'
    )
    assert it is not None
    assert it.needs_confirm is False  # soft write — no confirm forced


def test_terminal_status_without_milestone_id_stays_soft():
    # a terminal status with no milestone_id cannot be forced (needs an id to track/resolve)
    it = parse_interpretation('{"phase": "exfiltration", "status": "COMPLETE"}')
    assert it is not None
    assert it.milestone_id == "" and it.needs_confirm is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_parser.py -v -k "terminal or soft_status"`
Expected: FAIL — `test_terminal_status_forces_confirm_even_if_model_said_false` and the case-insensitive/DONE tests fail (`needs_confirm is False`, `prompt == ""`), because the current parser trusts the model's `needs_confirm`.

- [ ] **Step 3: Add the module constant**

In `redrobin/application/agents/assistant/parser.py`, immediately after `_VALID_SEVERITIES = {s.value for s in Severity}` (line 25), add:

```python

# A terminal/commit status ALWAYS asks the operator before it is written — enforce-by-
# construction, mirroring the #28 false-commit hardening (the model is unreliable at setting
# needs_confirm for a close). Soft statuses (in_progress / progress bumps) stay auto-write.
_COMMIT_STATUSES = {"COMPLETE", "COMPLETED", "DONE", "ACHIEVED", "CLOSED", "RESOLVED"}
```

- [ ] **Step 4: Rewrite the tail of `parse_interpretation`**

Replace lines 183–194 (from the `# A confirm is always ABOUT...` comment through the `return Interpretation(...)` block) with:

```python
    # A confirm is always ABOUT a specific objective/kill-chain id — without one it
    # can't be tracked/resolved, so an empty-id confirm downgrades to a soft auto-write
    # (prevents an untrackable empty-id pending a plain operator answer could collide with).
    status = str(data.get("status") or "").strip()
    prompt = str(data.get("prompt") or "").strip()
    needs_confirm = bool(data.get("needs_confirm")) and bool(milestone_id)
    if milestone_id and status.upper() in _COMMIT_STATUSES:
        needs_confirm = True  # a close/commit ALWAYS asks — enforce, don't trust the model
        if not prompt:
            prompt = f"Mark {milestone_id} {status}?"  # synthesize if the model omitted it
    return Interpretation(
        phase=phase,
        milestone_id=milestone_id,
        status=status,
        progress_pct=progress,
        needs_confirm=needs_confirm,
        prompt=prompt,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_parser.py -v`
Expected: PASS — all new tests green, and the existing interpretation tests (`test_parse_interpretation_recovers_from_prose_preamble`, `test_parse_interpretation_reserved_id_dropped_but_phase_kept_downgrades_confirm`, `test_parse_interpretation_semantic_milestone_id_unchanged`) stay green (they use no terminal status, so behaviour is unchanged).

- [ ] **Step 6: Run the mypy gate on the touched layer**

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

- [ ] **Step 7: Commit**

```bash
git add redrobin/application/agents/assistant/parser.py tests/unit/agents/assistant/test_parser.py
git commit -m "feat(assistant): force confirm-on-close for terminal milestone statuses"
```

---

### Task 2: `assistant.thinking` emit around each LLM cycle

**Files:**
- Modify: `redrobin/application/agents/assistant/assistant.py` (add `import contextlib`; add `AsyncIterator` to the `collections.abc` import; add `_emit_thinking` + `_thinking` methods; wrap the 4 LLM calls in `_on_enrichment`, `_on_question`, `_safe_interpret`, `_maybe_proactive`)
- Test: `tests/unit/agents/assistant/test_assistant_thinking.py` (new)

**Interfaces:**
- Consumes: `Assistant._bus.publish(event: dict)`; the four cycle functions `run_cycle`, `run_answer_cycle`, `run_interpretation_cycle` (existing).
- Produces: `Assistant._emit_thinking(state: str) -> None` (never-crash publish of `assistant.thinking`); `Assistant._thinking()` — an `@contextlib.asynccontextmanager` that publishes `start` on enter, `stop` on exit (in a `finally`). The bus emits `{"type": "assistant.thinking", "payload": {"state": "start"|"stop"}}` bracketing each model call, BEFORE the cycle's own `advisor.recommendation` / `operator.answer` / milestone publish.

**NOTE — no `tui_ws` change is needed.** `tui_ws.py`'s outbound loop forwards EVERY bus event to the TUI via `_send` (it is type-agnostic; only the INBOUND path is allowlisted by `_TUI_ALLOWED`). `advisor.recommendation` — another bus-only internal event — already relies on this exact mechanism, so `assistant.thinking` is forwarded with no code change. Do not edit `tui_ws.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/assistant/test_assistant_thinking.py`:

```python
from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"loud EDR",'
    '"opsec_risk":"WARN","confidence":"medium","references":["wm"]}'
)


class _RecLLM:
    """Returns a valid recommendation for any cycle (so the wrapped cycle publishes)."""

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        return _REC_JSON


def _assistant(bus, *, llm):
    return Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 1000.0),
        summary_reader=lambda: "recon done",
        enabled=True,
        clock=lambda: 1000.0,
    )


@pytest.mark.asyncio
async def test_thinking_brackets_the_llm_cycle():
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, llm=_RecLLM())
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    evts = []
    while not sub.empty():
        evts.append(sub.get_nowait())
    # start brackets BEFORE the model call, stop AFTER it, then the recommendation lands
    assert [e["type"] for e in evts] == [
        "assistant.thinking",
        "assistant.thinking",
        "advisor.recommendation",
    ]
    assert evts[0]["payload"]["state"] == "start"
    assert evts[1]["payload"]["state"] == "stop"


@pytest.mark.asyncio
async def test_thinking_emit_failure_never_breaks_the_cycle():
    class _ThinkBoomBus:
        def __init__(self):
            self.published = []

        async def publish(self, evt):
            if evt.get("type") == "assistant.thinking":
                raise RuntimeError("thinking publish boom")
            self.published.append(evt)

    bus = _ThinkBoomBus()
    a = _assistant(bus, llm=_RecLLM())
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    # a raising thinking-publish is swallowed; the cycle still completes + publishes its rec
    await a._maybe_proactive("test")
    assert any(e["type"] == "advisor.recommendation" for e in bus.published)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_thinking.py -v`
Expected: FAIL — `test_thinking_brackets_the_llm_cycle` fails because only `advisor.recommendation` is published (no `assistant.thinking` events yet); the list equality assertion fails.

- [ ] **Step 3: Add the imports**

In `redrobin/application/agents/assistant/assistant.py`:

Change line 13 area — after `import asyncio` add `import contextlib`:

```python
import asyncio
import contextlib
import logging
import time
```

Change the `collections.abc` import (line 16) to add `AsyncIterator`:

```python
from collections.abc import AsyncIterator, Callable
```

- [ ] **Step 4: Add the `_emit_thinking` + `_thinking` methods**

Insert these two methods immediately after `_on_confirm` (after its `logger.info(...)` block ends at line 313, before `_lull_timeout` at line 315):

```python
    async def _emit_thinking(self, state: str) -> None:
        """Publish the transient working signal (state 'start' / 'stop'). NEVER-crash: a
        thinking signal must never break a cycle, so a publish failure is logged + swallowed."""
        try:
            await self._bus.publish({"type": "assistant.thinking", "payload": {"state": state}})
        except Exception:
            logger.exception("assistant: thinking signal publish failed")

    @contextlib.asynccontextmanager
    async def _thinking(self) -> AsyncIterator[None]:
        """Bracket an LLM-backed cycle with assistant.thinking start/stop. The stop is in a
        finally, so a raising cycle still clears the indicator (the spinner never sticks)."""
        await self._emit_thinking("start")
        try:
            yield
        finally:
            await self._emit_thinking("stop")

```

- [ ] **Step 5: Wrap the reactive cycle (`_on_enrichment`)**

Replace the `rec = await run_cycle(...)` block in `_on_enrichment` (lines 181–190) with the same call wrapped in `async with self._thinking():`:

```python
        async with self._thinking():
            rec = await run_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                trigger_command=trigger_command,
                correlation_id=trigger_key,
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
            )
```

- [ ] **Step 6: Wrap the conversational cycle (`_on_question`)**

Replace the `parsed = await run_answer_cycle(...)` block in `_on_question` (lines 216–224) with:

```python
        async with self._thinking():
            parsed = await run_answer_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                question=question,
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
            )
```

- [ ] **Step 7: Wrap the interpretation cycle (`_safe_interpret`)**

Replace the `it = await run_interpretation_cycle(...)` block in `_safe_interpret` (lines 266–273) with the wrapped form (note: this call is already inside a `try:`, so the `async with` nests one level deeper — indent accordingly):

```python
            async with self._thinking():
                it = await run_interpretation_cycle(
                    llm=self._llm,
                    tools=self._tools,
                    wm_snapshot=self._wm_snapshot,
                    l5_summary=l5,
                    max_tokens=self._max_tokens,
                    wm_max_items=self._wm_max_items,
                )
```

- [ ] **Step 8: Wrap the proactive cycle (`_maybe_proactive`)**

Replace the `rec = await run_cycle(...)` block in `_maybe_proactive` (lines 354–364) with:

```python
        async with self._thinking():
            rec = await run_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                trigger_command="",
                correlation_id=None,
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
                proactive=True,
            )
```

- [ ] **Step 9: Run the new tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/test_assistant_thinking.py -v`
Expected: PASS — both tests green.

- [ ] **Step 10: Run the assistant regression tests + mypy**

Run: `uv run --no-sync pytest tests/unit/agents/assistant/ tests/integration/test_assistant.py -v`
Expected: PASS — the proactive/enrichment/interpret tests are unaffected (they drain `advisor.recommendation` / `operator.*` specifically, ignoring the extra `assistant.thinking` frames; `test_material_change_backstops_when_reactive_silent` filters by `type == "advisor.recommendation"`, so the interleaved thinking events don't perturb its count).

Run: `uv run --no-sync mypy redrobin/domain redrobin/application`
Expected: `Success: no issues found`

- [ ] **Step 11: Commit**

```bash
git add redrobin/application/agents/assistant/assistant.py tests/unit/agents/assistant/test_assistant_thinking.py
git commit -m "feat(assistant): emit assistant.thinking around each LLM cycle"
```

---

### Task 3: Prominent non-blocking confirm bar + resolution trace (TUI)

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py` (imports; module constants; `ChatPane.__init__`; `ChatPane.compose`; `ChatPane.on_input_submitted`; new `ChatPane._refresh_confirm_bar`; replace `format_confirm` with `format_confirm_bar`; add `format_resolution`; rewrite `_on_question`; update `__all__`)
- Modify: `redrobin/tui/textual_ui/theme.tcss` (add `#chat-bottom` + `#confirm-bar`; drop `dock: bottom` from `#chat-input`)
- Test: `tests/unit/tui_textual/test_chat.py` (update imports; replace `test_format_confirm_shows_yn_affordance`; update `test_operator_question_confirm_renders_and_tracks_pending`; add renderer + reveal/resolve tests)

**Interfaces:**
- Consumes: `ChatPane._pending: list[str]` (existing); `is_confirm_question`, `_milestone_id`, `_chat` (existing); `next_operator_message`, `format_operator_echo` (existing, unchanged); Textual `Vertical`, `Static`.
- Produces:
  - `_PLACEHOLDER_IDLE = "Ask the assistant…  (/y or /n to a confirm)"` and `_PLACEHOLDER_CONFIRM = "▶ Confirm pending — reply /y or /n"` (module constants).
  - `ChatPane._pending_prompts: dict[str, str]` — milestone_id → its confirm question text.
  - `ChatPane._refresh_confirm_bar() -> None` — reveal/hide `#confirm-bar` + flip the input placeholder from `_pending` (reflects `_pending[0]`, the confirm `/y`/`/n` resolves).
  - `format_confirm_bar(question: str) -> Text` (replaces `format_confirm`); `format_resolution(milestone_id: str, confirmed: bool) -> Text`.
  - New widgets in the tree: `#chat-bottom` (a `Vertical`, docked bottom) wrapping `#confirm-bar` (hidden `Static`, border-title "Red Robin needs your call") + `#chat-input`.

- [ ] **Step 1: Confirm `format_confirm` has no callers outside chat.py + its test**

Run: `uv run --no-sync python -c "import subprocess"` is not needed — instead grep:
Run (Grep tool or): `git grep -n "format_confirm\b" -- "*.py"`
Expected: matches ONLY in `redrobin/tui/textual_ui/chat.py` (definition, `_on_question` call, `__all__`) and `tests/unit/tui_textual/test_chat.py` (import + `test_format_confirm_shows_yn_affordance`). If any other caller exists, stop and update it too.

- [ ] **Step 2: Write / update the failing tests**

In `tests/unit/tui_textual/test_chat.py`:

(a) Update the top imports — add `Static`, swap `format_confirm` → `format_confirm_bar`, add `format_resolution` + the two placeholder constants:

```python
from textual.widgets import Input, RichLog, Static

from redrobin.tui.textual_ui.chat import (
    ChatPane,
    _PLACEHOLDER_CONFIRM,
    _PLACEHOLDER_IDLE,
    format_answer,
    format_confirm_bar,
    format_operator_echo,
    format_recommendation,
    format_resolution,
    is_confirm_question,
    next_operator_message,
    register_chat,
)
```

(b) Replace `test_format_confirm_shows_yn_affordance` (lines 28–33) with:

```python
def test_format_confirm_bar_shows_yn_affordance():
    s = format_confirm_bar("Mark obj-1 COMPLETE?").plain
    assert "Mark obj-1 COMPLETE?" in s
    assert "/y" in s.lower() and "/n" in s.lower()


def test_format_resolution_confirmed_and_declined():
    ok = format_resolution("obj-1", True).plain
    assert "obj-1" in ok and "confirmed" in ok.lower()
    no = format_resolution("obj-1", False).plain
    assert "obj-1" in no and "declined" in no.lower()
```

(c) Replace the body of `test_operator_question_confirm_renders_and_tracks_pending` (the confirm no longer renders as a chat-log line — it reveals the bar):

```python
@pytest.mark.asyncio
async def test_operator_question_confirm_renders_and_tracks_pending():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {
                    "text": "Obj DA COMPLETE?",
                    "context_refs": ["milestone:obj1"],
                    "source": "assistant",
                },
            },
        )
        pane = pilot.app.query_one("#events", ChatPane)
        assert pane._pending == ["obj1"]
        bar = pane.query_one("#confirm-bar", Static)
        assert bar.display is True  # the confirm reveals the prominent bar...
        assert pane._pending_prompts["obj1"] == "Obj DA COMPLETE?"  # ...tracking the question
        assert len(pane.query_one("#chat-log", RichLog).lines) == 0  # NOT a chat line anymore
        # an operator-sourced echo must NOT track or reveal
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {"text": "x", "context_refs": ["milestone:z"], "source": "operator"},
            },
        )
        assert pane._pending == ["obj1"]
```

(d) Append the full reveal→resolve→hide Pilot test (uses `_SendHarness`, defined later in the file — move this test below the `_SendHarness` class definition, or place it at end of file):

```python
@pytest.mark.asyncio
async def test_confirm_bar_reveals_on_question_and_hides_on_resolve():
    reg = Registry()
    register_chat(reg)
    async with _SendHarness(sent_result=True).run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        bar = pane.query_one("#confirm-bar", Static)
        inp = pane.query_one("#chat-input", Input)
        assert bar.display is False and inp.placeholder == _PLACEHOLDER_IDLE  # idle at rest
        reg.dispatch(
            target=pilot.app,
            frame={
                "type": "operator.question",
                "payload": {
                    "text": "Mark obj-1 COMPLETE?",
                    "context_refs": ["milestone:obj-1"],
                    "source": "assistant",
                },
            },
        )
        assert bar.display is True  # revealed
        assert pane._pending_prompts["obj-1"] == "Mark obj-1 COMPLETE?"  # bar renders this prompt
        assert inp.placeholder == _PLACEHOLDER_CONFIRM  # placeholder reflects the pending confirm
        assert pane._pending == ["obj-1"]
        # resolve with /y -> pending consumed, bar hides, placeholder restores, trace in the log
        await pane.on_input_submitted(Input.Submitted(inp, "/y"))
        assert pane._pending == []
        assert bar.display is False
        assert inp.placeholder == _PLACEHOLDER_IDLE
        # the echo ("/y") + the resolution trace both landed in the chat log
        assert len(pane.query_one("#chat-log", RichLog).lines) >= 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v -k "confirm_bar or format_confirm_bar or format_resolution or tracks_pending"`
Expected: FAIL — `ImportError` (no `format_confirm_bar` / `format_resolution` / placeholder constants), and once imports resolve, `#confirm-bar` is `NoMatches`.

- [ ] **Step 4: Update `chat.py` imports + module constants**

Change the container import (line 15) and widgets import (line 16):

```python
from textual.containers import Container, Vertical
from textual.widgets import Input, RichLog, Static
```

After the colour constants block (after `_RISK_COLORS = {...}`, line 24), add:

```python

_PLACEHOLDER_IDLE = "Ask the assistant…  (/y or /n to a confirm)"
_PLACEHOLDER_CONFIRM = "▶ Confirm pending — reply /y or /n"
```

- [ ] **Step 5: Update `ChatPane.__init__` + `compose`**

Replace `__init__` (lines 30–33) to track prompts:

```python
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending: list[str] = []
        self._pending_prompts: dict[str, str] = {}  # milestone_id -> its confirm question text
        self._q_counter = 0
```

Replace `compose` (lines 35–41) — wrap the input in a docked `#chat-bottom` `Vertical`, with the reserved `#confirm-bar` above it:

```python
    def compose(self) -> ComposeResult:
        log = RichLog(
            id="chat-log", highlight=False, markup=False, wrap=True, min_width=0, max_lines=2000
        )
        log.can_focus = False  # only the Input is a tab stop (the scrollback isn't interactive)
        yield log
        with Vertical(id="chat-bottom"):
            bar = Static("", id="confirm-bar")  # reserved; revealed only while a confirm is pending
            bar.can_focus = False
            bar.border_title = "Red Robin needs your call"
            yield bar
            yield Input(id="chat-input", placeholder=_PLACEHOLDER_IDLE)
```

- [ ] **Step 6: Add `_refresh_confirm_bar` + rewrite `on_input_submitted`**

Replace `on_input_submitted` (lines 49–63) with the version that logs a resolution trace + refreshes the bar, and add `_refresh_confirm_bar` right after it:

```python
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        kind, msg = next_operator_message(text, pending=self._pending, q_index=self._q_counter)
        self.append_line(format_operator_echo(text))
        sent = await self.app.send_operator_message(msg)  # type: ignore[attr-defined]
        # A question bumps the monotonic local counter unconditionally (harmless on a failed
        # send). A confirm answer consumes the pending it targeted ONLY on a confirmed send —
        # otherwise a re-typed /y after reconnect must still be able to resolve it (#28 #2) —
        # and drops a durable one-line trace of the decision into the chat (OD-1).
        if kind == "question":
            self._q_counter += 1
        elif sent and msg["question_id"] in self._pending:
            resolved = msg["question_id"]
            self._pending.remove(resolved)
            self._pending_prompts.pop(resolved, None)
            self.append_line(format_resolution(resolved, msg["text"] == "yes"))
        self._refresh_confirm_bar()

    def _refresh_confirm_bar(self) -> None:
        """Reveal/hide the prominent confirm bar + flip the input placeholder from the current
        _pending. The bar reflects _pending[0] — the confirm a /y or /n resolves. Never-crash
        (pre-mount safe: a query before mount is swallowed)."""
        try:
            bar = self.query_one("#confirm-bar", Static)
            inp = self.query_one("#chat-input", Input)
        except Exception:
            return
        if self._pending:
            head = self._pending[0]
            bar.update(format_confirm_bar(self._pending_prompts.get(head, head)))
            bar.display = True
            inp.placeholder = _PLACEHOLDER_CONFIRM
        else:
            bar.display = False
            inp.placeholder = _PLACEHOLDER_IDLE
```

- [ ] **Step 7: Replace `format_confirm` with `format_confirm_bar` + add `format_resolution`**

Replace `format_confirm` (lines 111–118) with:

```python
def format_confirm_bar(question: str) -> Text:
    """The prominent pending-confirm content, rendered inside #confirm-bar (which carries the
    'Red Robin needs your call' border title). Distinct from a chat line: the question, then
    the explicit /y /n affordance."""
    t = Text()
    t.append(question or "Confirm?", style=f"bold {_CONFIRM}")
    t.append("     ")
    t.append("/y", style="bold #3fb950")
    t.append(" yes   ", style=_MUTED)
    t.append("/n", style="bold #f85149")
    t.append(" no", style=_MUTED)
    return t


def format_resolution(milestone_id: str, confirmed: bool) -> Text:
    """A durable one-line trace of a resolved confirm in the chat log (OD-1). The resulting
    milestone status shows in the Working-Memory panel; this records the operator's call."""
    t = Text()
    if confirmed:
        t.append("✓ ", style="bold #3fb950")
        t.append(f"{milestone_id} confirmed", style="#3fb950")
    else:
        t.append("✗ ", style="bold #f85149")
        t.append(f"{milestone_id} declined", style="#f85149")
    return t
```

- [ ] **Step 8: Rewrite `_on_question` to drive the bar (not a chat line)**

Replace `_on_question` (lines 179–188) with:

```python
def _on_question(app: Any, payload: dict[str, Any]) -> None:
    if payload.get("source") == "operator" or not is_confirm_question(payload):
        return  # a plain operator ask (its own echo) — skip; only assistant confirms render
    pane = _chat(app)
    if pane is None:
        return
    mid = _milestone_id(payload)
    if mid and mid not in pane._pending:
        pane._pending.append(mid)
    if mid:
        pane._pending_prompts[mid] = str(payload.get("text") or "")
    pane._refresh_confirm_bar()  # reveal the prominent bar + flip the placeholder
```

- [ ] **Step 9: Update `__all__`**

In the `__all__` list (lines 197–206), replace `"format_confirm"` with `"format_confirm_bar"` and add `"format_resolution"` (keep the list alphabetically ordered):

```python
__all__ = [
    "ChatPane",
    "format_answer",
    "format_confirm_bar",
    "format_operator_echo",
    "format_recommendation",
    "format_resolution",
    "is_confirm_question",
    "next_operator_message",
    "register_chat",
]
```

- [ ] **Step 10: Update the theme CSS**

In `redrobin/tui/textual_ui/theme.tcss`, replace the `#chat-input` rule (line 46) and add the `#chat-bottom` + `#confirm-bar` rules. The block at lines 42–46 becomes:

```css
/* #28 Chat pane (replaces the Verdicts/Events pane). The #events Container keeps
   its width + round border; style the inner scrollback + the docked bottom stack. The log
   never scrolls horizontally — long lines wrap to the pane (wrap=True + min_width=0). */
#chat-log { overflow-x: hidden; overflow-y: auto; height: 1fr; }

/* The bottom stack: a prominent (reserved) confirm bar above the input, docked together so
   the log's 1fr shrinks when the bar reveals. The bar uses the reveal pattern the old
   #advisor-dock used (display:none until a confirm is pending — toggled in code). */
#chat-bottom { dock: bottom; height: auto; }
#confirm-bar {
    display: none;
    height: auto;
    border: round $status-warn;
    border-title-color: $status-warn;
    padding: 0 1;
    margin: 0 0 1 0;
}
#chat-input { height: 3; border: round $panel-lighten-2; }
```

- [ ] **Step 11: Run the chat tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v`
Expected: PASS — the new reveal/resolve + renderer tests are green, and the untouched routing/consume tests (`test_next_operator_message_routes_ask_vs_confirm`, `test_pending_consumed_when_send_confirmed`, `test_pending_not_consumed_when_send_fails`, `test_chat_pane_composes_and_appends`, `test_recommendation_routes_to_chat_and_dock_is_gone`) stay green.

- [ ] **Step 12: Commit**

```bash
git add redrobin/tui/textual_ui/chat.py redrobin/tui/textual_ui/theme.tcss tests/unit/tui_textual/test_chat.py
git commit -m "feat(tui): prominent non-blocking confirm bar + resolution trace"
```

---

### Task 4: 200ms-delayed auto-erasing thinking spinner (TUI)

**Files:**
- Modify: `redrobin/tui/textual_ui/chat.py` (import `Timer`; add `_THINK_DELAY` + `_SPIN_FRAMES` + `_thinking_text`; add `#thinking-status` to `compose`; add spinner state to `__init__`; add `start_thinking`/`stop_thinking`/`_reveal_thinking`/`_tick_thinking`/`_cancel_timer`; add `_on_thinking` handler; register `assistant.thinking` in `register_chat`)
- Modify: `redrobin/tui/textual_ui/theme.tcss` (add `#thinking-status`)
- Test: `tests/unit/tui_textual/test_chat.py` (append thinking tests)

**Interfaces:**
- Consumes: Task 3's `#chat-bottom` `Vertical` (the spinner mounts inside it, between `#confirm-bar` and `#chat-input`); `_chat(app)` (existing); Textual `Widget.set_timer`/`set_interval` (return `Timer`).
- Produces:
  - `_THINK_DELAY = 0.2`; `_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"`; `_thinking_text(frame: int) -> Text`.
  - `ChatPane._thinking: int` (active-cycle count), `_think_timer`/`_spin_timer: Timer | None`, `_spin_i: int`.
  - `ChatPane.start_thinking() -> None` (arm the 200ms delayed reveal, idempotent/nesting-safe); `stop_thinking() -> None` (decrement; when none remain, cancel the pending reveal + hide + stop the animation); `_reveal_thinking()`, `_tick_thinking()`, `_cancel_timer(attr: str)` (helpers).
  - `_on_thinking(app, payload)` — routes `state=="start"` → `start_thinking()`, `state=="stop"` → `stop_thinking()`; registered on `assistant.thinking`.
  - New widget: `#thinking-status` (hidden `Static`, `can_focus=False`) in `#chat-bottom`.

- [ ] **Step 1: Write the failing tests**

First extend the chat imports at the top of `tests/unit/tui_textual/test_chat.py` — add `_SPIN_FRAMES` and `_thinking_text` to the `from redrobin.tui.textual_ui.chat import (...)` block (keep it ordered):

```python
    _PLACEHOLDER_CONFIRM,
    _PLACEHOLDER_IDLE,
    _SPIN_FRAMES,
    _thinking_text,
    format_answer,
```

Then append to `tests/unit/tui_textual/test_chat.py`:

```python
# ---- interactivity increment: the "thinking" spinner (assistant.thinking) ----


def test_register_chat_registers_thinking():
    reg = Registry()
    register_chat(reg)
    assert reg.has_handler("assistant.thinking")


def test_thinking_text_has_label_and_spinner():
    # the transient line leads with a braille spinner glyph + a generic label (pure renderer;
    # asserted here because Textual 8.2.7's Static has no .renderable read-back)
    t = _thinking_text(0).plain
    assert "thinking" in t.lower()
    assert t[0] in _SPIN_FRAMES  # leads with a spinner glyph


@pytest.mark.asyncio
async def test_on_thinking_start_arms_and_stop_cancels():
    reg = Registry()
    register_chat(reg)
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        reg.dispatch(
            target=pilot.app,
            frame={"type": "assistant.thinking", "payload": {"state": "start"}},
        )
        assert pane._thinking == 1
        reg.dispatch(
            target=pilot.app,
            frame={"type": "assistant.thinking", "payload": {"state": "stop"}},
        )
        assert pane._thinking == 0
        assert pane._think_timer is None  # stop cancelled the pending 200ms reveal


@pytest.mark.asyncio
async def test_thinking_reveals_then_hides():
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        status = pane.query_one("#thinking-status", Static)
        assert status.display is False  # hidden at rest
        pane._thinking = 1  # simulate a cycle in progress
        pane._reveal_thinking()  # simulate the 200ms timer firing
        assert status.display is True
        assert pane._spin_timer is not None  # the glyph animation started on reveal
        pane.stop_thinking()  # the cycle ended
        assert status.display is False
        assert pane._spin_timer is None  # the animation interval was stopped


@pytest.mark.asyncio
async def test_thinking_fast_cycle_no_flash():
    # a cycle that finishes before the 200ms delay never reveals the spinner (no flash)
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        pane.start_thinking()  # arms the 200ms timer
        pane.stop_thinking()  # ends before 200ms -> cancels the timer, never reveals
        assert pane.query_one("#thinking-status", Static).display is False
        assert pane._think_timer is None


@pytest.mark.asyncio
async def test_thinking_is_display_only_never_steals_input_focus():
    async with _ChatHarness().run_test() as pilot:
        pane = pilot.app.query_one("#events", ChatPane)
        inp = pane.query_one("#chat-input", Input)
        inp.focus()
        await pilot.pause()
        pane._thinking = 1
        pane._reveal_thinking()  # reveal the spinner
        await pilot.pause()
        assert pane.query_one("#thinking-status", Static).display is True
        assert pane.query_one("#thinking-status", Static).can_focus is False
        assert pilot.app.focused is inp  # focus never left the input — the spinner is display-only
        pane.stop_thinking()  # tidy: stop the animation timer before teardown
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v -k thinking`
Expected: FAIL — `register_chat` has no `assistant.thinking` handler; `#thinking-status` is `NoMatches`; `ChatPane` has no `_thinking`/`start_thinking`/`_reveal_thinking`.

- [ ] **Step 3: Import `Timer` + add spinner constants and the pure renderer**

In `chat.py`, add the Timer import after the widgets import (line 16 area):

```python
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static
```

After the `_PLACEHOLDER_*` constants (added in Task 3), add:

```python
_THINK_DELAY = 0.2  # show only after 200ms — avoid a flash on fast cycles (TUI best practice)
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _thinking_text(frame: int) -> Text:
    """The transient working-indicator line: a braille spinner glyph + a generic label."""
    glyph = _SPIN_FRAMES[frame % len(_SPIN_FRAMES)]
    t = Text()
    t.append(f"{glyph} ", style=_ACCENT)
    t.append("Red Robin is thinking…", style=_MUTED)
    return t
```

- [ ] **Step 4: Add spinner state to `__init__`**

Extend `ChatPane.__init__` (after `self._q_counter = 0`) with the spinner fields:

```python
        self._thinking = 0  # active LLM-cycle count (start/stop are balanced, may nest)
        self._think_timer: Timer | None = None  # the 200ms delayed-reveal handle
        self._spin_timer: Timer | None = None  # the glyph animation interval
        self._spin_i = 0
```

- [ ] **Step 5: Add `#thinking-status` to `compose`**

In `compose`, inside the `with Vertical(id="chat-bottom"):` block, add the hidden status line BETWEEN the confirm bar and the input:

```python
        with Vertical(id="chat-bottom"):
            bar = Static("", id="confirm-bar")  # reserved; revealed only while a confirm is pending
            bar.can_focus = False
            bar.border_title = "Red Robin needs your call"
            yield bar
            status = Static("", id="thinking-status")  # reserved; revealed 200ms into a cycle
            status.can_focus = False
            yield status
            yield Input(id="chat-input", placeholder=_PLACEHOLDER_IDLE)
```

- [ ] **Step 6: Add the thinking-spinner methods**

Add these methods to `ChatPane` (place them after `_refresh_confirm_bar`):

```python
    def start_thinking(self) -> None:
        """A cycle began. Arm the 200ms delayed reveal; idempotent while already armed/shown
        (nesting-safe — balanced with stop_thinking). Never-crash."""
        self._thinking += 1
        if self._thinking == 1 and self._think_timer is None:
            try:
                self._think_timer = self.set_timer(_THINK_DELAY, self._reveal_thinking)
            except Exception:
                pass

    def stop_thinking(self) -> None:
        """A cycle ended. When no cycle remains, cancel a still-pending reveal, hide the
        spinner, and stop the animation. Idempotent (a stop with no start is a no-op)."""
        if self._thinking > 0:
            self._thinking -= 1
        if self._thinking > 0:
            return
        self._cancel_timer("_think_timer")
        self._cancel_timer("_spin_timer")
        try:
            self.query_one("#thinking-status", Static).display = False
        except Exception:
            pass

    def _reveal_thinking(self) -> None:
        """Fired 200ms after the first start. If a cycle is still running, reveal the spinner
        and start the glyph animation; if it already finished, do nothing (no flash)."""
        self._think_timer = None
        if self._thinking <= 0:
            return  # the cycle finished within the 200ms window — never reveal
        try:
            status = self.query_one("#thinking-status", Static)
        except Exception:
            return
        self._spin_i = 0
        status.update(_thinking_text(self._spin_i))
        status.display = True
        try:
            self._spin_timer = self.set_interval(0.1, self._tick_thinking)
        except Exception:
            pass

    def _tick_thinking(self) -> None:
        """Advance the spinner glyph (display-only)."""
        self._spin_i += 1
        try:
            self.query_one("#thinking-status", Static).update(_thinking_text(self._spin_i))
        except Exception:
            pass

    def _cancel_timer(self, attr: str) -> None:
        """Stop + clear a stored Timer handle (self._think_timer / self._spin_timer)."""
        timer = getattr(self, attr, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            setattr(self, attr, None)
```

- [ ] **Step 7: Add the `_on_thinking` handler + register it**

Add the handler after `_on_recommendation` (near the other `_on_*` handlers):

```python
def _on_thinking(app: Any, payload: dict[str, Any]) -> None:
    pane = _chat(app)
    if pane is None:
        return
    state = str(payload.get("state") or "")
    if state == "start":
        pane.start_thinking()
    elif state == "stop":
        pane.stop_thinking()
```

Add the registration line to `register_chat`:

```python
def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)
    registry.register("advisor.recommendation")(_on_recommendation)
    registry.register("assistant.thinking")(_on_thinking)
```

- [ ] **Step 8: Add the theme CSS for `#thinking-status`**

In `redrobin/tui/textual_ui/theme.tcss`, after the `#chat-input` rule (added/edited in Task 3), add:

```css
/* Transient "thinking" indicator — a thin line above the input, revealed only while a cycle
   runs (200ms-delayed + auto-erased in code). Display-only (no focus, no input delay). */
#thinking-status { display: none; height: 1; color: $text-muted; padding: 0 1; }
```

- [ ] **Step 9: Run the thinking tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v -k thinking`
Expected: PASS — all five thinking tests green.

- [ ] **Step 10: Run the full chat test file**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_chat.py -v`
Expected: PASS — Task 3's confirm-bar tests and every untouched test stay green.

- [ ] **Step 11: Commit**

```bash
git add redrobin/tui/textual_ui/chat.py redrobin/tui/textual_ui/theme.tcss tests/unit/tui_textual/test_chat.py
git commit -m "feat(tui): 200ms-delayed auto-erasing thinking spinner"
```

---

### Final: full CI gate + branch review

- [ ] **Step 1: Run all 5 CI gates**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -v --tb=short
```

Expected: all green — ruff clean, format clean, mypy `Success`, import-linter `Contracts: 4 kept, 0 broken`, pytest all passed (780+ existing + the new tests).

- [ ] **Step 2: Whole-branch review** (subagent-driven-development dispatches the final code reviewer here) — then the operator re-smoke (the ONLY acceptance for the real look/feel): confirm reads as a clear ask, a close asks before committing, the spinner shows while thinking, input stays responsive throughout.

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:

| Spec | Task |
|------|------|
| §4.A `_COMMIT_STATUSES` + force `needs_confirm` + synthesize prompt | Task 1 (steps 3–4) |
| §4.A soft writes stay auto | Task 1 (`test_soft_status_stays_auto_write`) |
| §4.B reserved `#confirm-bar` between log + input, revealed on pending, distinct (accent border + "Red Robin needs your call") | Task 3 (compose, tcss, `_refresh_confirm_bar`) |
| §4.B input placeholder flips on pending / restores on clear | Task 3 (`_PLACEHOLDER_*`, `_refresh_confirm_bar`) |
| §4.B drives off the same `_pending`; `/y /n` routing + false-commit UNCHANGED | Task 3 (`on_input_submitted` keeps `next_operator_message` + consume-on-confirmed-send; routing tests untouched) |
| §4.B NOT modal (non-blocking) | Task 3 (`#confirm-bar` is `can_focus=False` display + a docked bar; the Input still owns `/y /n`) |
| OD-1 (resolved: DO log a one-line outcome on resolve) | Task 3 (`format_resolution`, appended in `on_input_submitted`) |
| §4.C new `assistant.thinking` bus event `{state}` | Task 2 (`_emit_thinking`) |
| §4.C emit around ALL four cycles, never-crash | Task 2 (`_thinking` ctxmgr wraps `_on_enrichment`/`_on_question`/`_safe_interpret`/`_maybe_proactive`; `_emit_thinking` swallows) |
| §4.C `tui_ws` forwards it | Task 2 NOTE (automatic — type-agnostic outbound loop; `advisor.recommendation` already relies on it) |
| §4.C spinner after ~200ms, auto-erase, never delay input; idempotent start/stop | Task 4 (`_THINK_DELAY`, `start_thinking`/`stop_thinking` nesting-count, `_reveal_thinking` no-flash guard, `can_focus=False`) |
| OD-2 (resolved: spinner at ChatPane bottom above input) | Task 4 (`#thinking-status` in `#chat-bottom`, between bar + input) |
| OD-3 (resolved: generic "thinking…" label) | Task 4 (`_thinking_text` → "Red Robin is thinking…") |
| §7 DoD unit tests (A: COMPLETE forced + prompt; in_progress soft; model prompt preserved) | Task 1 tests |
| §7 DoD Pilot (confirm reveals + placeholder; resolve hides) | Task 3 `test_confirm_bar_reveals_on_question_and_hides_on_resolve` |
| §7 DoD unit (cycle publishes start/stop; never-crash) | Task 2 tests |
| §7 DoD Pilot (spinner after delay + auto-erase; input focusable throughout) | Task 4 `test_thinking_reveals_then_hides` / `_fast_cycle_no_flash` / `_is_display_only_never_steals_input_focus` |

**2. Placeholder scan** — no `TBD`/`TODO`/"handle edge cases"/"similar to Task N"; every code step shows complete code; every command has an expected result.

**3. Type consistency** — `_refresh_confirm_bar` (Task 3) is referenced by `_on_question` + `on_input_submitted` (both Task 3). `#chat-bottom` (Task 3) hosts `#thinking-status` (Task 4). `format_confirm_bar`/`format_resolution` names match between chat.py, `__all__`, and the test imports. `assistant.thinking` payload shape `{"state": "start"|"stop"}` matches between Task 2 (emit), Task 4 (`_on_thinking` reads `payload["state"]`), and both test files. `Timer` import backs the `_think_timer`/`_spin_timer` annotations. `_thinking` is an `int` counter on `ChatPane` (Task 4) — distinct from the `Assistant._thinking()` context manager (Task 2, different class); no collision.

**4. Task independence** — Tasks 1 and 2 are pure `application` (independent of each other and of the TUI). Task 3 restructures the ChatPane compose; Task 4 depends structurally on Task 3's `#chat-bottom`. Order 1 → 2 → 3 → 4 respects the one dependency.
