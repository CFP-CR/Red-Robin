# TUI Textual Redesign — Phase 3 (Color + Chrome) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Textual TUI its situational-awareness chrome — a Header (TopBar) with daemon-connection status + live verdict counts, out-of-scope shown in red, a focus accent, and a graceful "terminal too small" hint below 80×24 — all driven by the existing reactive store, 16-color-safe.

**Architecture:** Phase 1 built the app shell + reactive store + WS worker + frame registry; Phase 2 filled the three panes (Sessions/Events/Memory) bound to reactive state. Phase 3 adds the **chrome** around them. The TopBar becomes a real widget rendering a Rich `Text` (app name · engagement · connection dot+label · verdict counts) refreshed by a single `_refresh_topbar()` that every relevant watcher calls. The WS worker reports connection transitions through a `set_state` callback into a new `connection_state` reactive. Color stays centralized: verdict/status/scope colors are module constants kept in sync with `theme.tcss`, and every colored token also carries a text label + weight so it survives non-truecolor terminals. Responsiveness is a pure `is_too_small(w,h)` policy toggled from `on_resize`.

**Tech Stack:** Python 3.11, Textual 8.2.7 (`App`/`reactive`/`watch_*`/`Static`/`Footer`/`events.Resize`/`run_test` Pilot), Rich `Text`, `.tcss` (semantic tokens + `:focus-within`), pytest (strict asyncio) + `uv run --no-sync`.

---

## Constraints (hard — every task)

- **Branch:** `tui/phase-3-color-chrome` off `dev-juan` (`247a3b6`). Build **alongside** the legacy prompt_toolkit client; the daemon, bus, reducer, pipeline, and `.cna` are **untouched**. Only `redrobin/tui/textual_ui/*` + its tests change.
- **Tests:** `uv run --no-sync pytest ...` (the operator daemon may hold the venv executable → `--no-sync`). Repo runs pytest in **strict asyncio** → every async test keeps `@pytest.mark.asyncio`.
- **Lint/format gates (CI runs these on `.`):** `uv run --no-sync ruff check .` and `uv run --no-sync ruff format --check .` must pass for every new/edited file. **Do NOT add `# noqa: BLE001`** — `BLE001` is not selected, so the noqa is unused → `RUF100` failure. `redrobin/tui/` is **not** mypy-gated (CI mypy covers `domain`+`application` only), but keep types clean anyway.
- **Commits:** atomic (one logical change per commit), conventional-commit subject, **no AI/Claude co-author trailer**.
- **Verification of new APIs (already done while writing this plan, against the installed Textual 8.2.7):** `events.Resize` has `.size` (a `Size` with `.width`/`.height`); `on_resize` is not defined on `App` by default → defining it is a clean override and it **fires at startup** under `run_test(size=(w,h))` with the real size; default `run_test()` size is **80×24**; `Widget.display` toggles visibility and a Python `.display = True` overrides a CSS `display: none`; `:focus-within` is a recognized pseudo-class (referenced in Textual's `constants.py`/`stylesheet.py`); `Static.content` (NOT `.renderable`) exposes the rendered renderable and `str(content)` of a Rich `Text` yields its plain text.

---

## File Structure

- **Modify** `redrobin/tui/textual_ui/panels.py` — add pure helpers `verdict_counts`, `format_topbar`, `format_memory`; add the `TopBar(Static)` widget; add color constants `_STATUS_COLORS`, `_SCOPE_OUT`; switch `MemoryPanel` to render `format_memory` (out-of-scope in red). *(Home of widgets + pure render helpers — consistent with `format_verdict`/`apply_verdict`.)*
- **Modify** `redrobin/tui/textual_ui/ws_worker.py` — add an optional `set_state: Callable[[str], None]` param to `ws_loop`; emit `"connecting"` / `"connected"` / (on reconnect) `"connecting"`.
- **Modify** `redrobin/tui/textual_ui/app.py` — add `connection_state` reactive; mount `TopBar` (replacing the plain `Static` topbar) + a hidden `#resize-hint`; add `_refresh_topbar()`, `watch_connection_state`, `_set_connection_state`, `on_resize`/`_apply_responsive`, and module-level `is_too_small`; wire `set_state` into the `ws_loop` call.
- **Modify** `redrobin/tui/textual_ui/theme.tcss` — style `#resize-hint` (centered, hidden) + a `:focus-within` accent border on the panes.
- **Modify** `tests/unit/tui_textual/test_panels.py` — tests for `verdict_counts`, `format_topbar`, `TopBar`, `format_memory` (out-of-scope red).
- **Modify** `tests/unit/tui_textual/test_ws_worker.py` — test `ws_loop` reports connection states.
- **Create** `tests/unit/tui_textual/test_app_phase3.py` — Pilot tests: TopBar shows engagement + counts; connection_state → TopBar; resize hint shows/hides.

---

## Task 1: TopBar render helpers + widget (panels.py)

**Files:**
- Modify: `redrobin/tui/textual_ui/panels.py`
- Test: `tests/unit/tui_textual/test_panels.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tui_textual/test_panels.py`:

```python
def test_verdict_counts_tallies_known_verdicts_only():
    events = [
        {"verdict": "BLOCK"},
        {"verdict": "BLOCK"},
        {"verdict": "PASS"},
        {"verdict": "WARN"},
        {"verdict": "ESCALATE"},
        {"verdict": "WAT"},  # unknown → ignored
        {},  # no verdict → ignored
    ]
    counts = panels.verdict_counts(events)
    assert counts == {"BLOCK": 2, "WARN": 1, "PASS": 1, "ESCALATE": 1}


def test_format_topbar_is_16color_safe_text():
    out = panels.format_topbar(
        "e2e-verify", "connected", {"BLOCK": 3, "WARN": 1, "PASS": 12, "ESCALATE": 0}
    )
    assert isinstance(out, Text)
    plain = out.plain
    # Every colored token also carries a text label (survives no-color terminals).
    assert "e2e-verify" in plain
    assert "connected" in plain
    assert "3 BLOCK" in plain
    assert "1 WARN" in plain
    assert "12 PASS" in plain
    assert "0 ESCALATE" in plain


def test_format_topbar_blank_engagement_shows_dash():
    out = panels.format_topbar("", "disconnected", {"BLOCK": 0, "WARN": 0, "PASS": 0, "ESCALATE": 0})
    # Blank engagement falls back to "-" (rendered between the middle-dot separators).
    assert " - " in out.plain


@pytest.mark.asyncio
async def test_topbar_widget_renders_status():
    class _BarApp(App):
        def compose(self) -> ComposeResult:
            yield panels.TopBar("Red Robin", id="bar")

    app = _BarApp()
    async with app.run_test():
        bar = app.query_one("#bar", panels.TopBar)
        bar.update_status("acme", "connected", {"BLOCK": 1, "WARN": 0, "PASS": 0, "ESCALATE": 0})
        content = str(bar.content)
        assert "acme" in content
        assert "1 BLOCK" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -k "verdict_counts or format_topbar or topbar_widget" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'verdict_counts'` / `'format_topbar'` / `'TopBar'`.

- [ ] **Step 3: Implement the helpers + widget**

In `redrobin/tui/textual_ui/panels.py`, add `_STATUS_COLORS` next to the existing color constants (after `_MUTED`):

```python
# Daemon-connection status colors — mirror $status-ok/$status-warn/$status-err in theme.tcss.
_STATUS_COLORS: dict[str, str] = {
    "connected": "#3fb950",
    "connecting": "#e3b341",
    "disconnected": "#f85149",
}
```

Then add the two pure helpers (place them after `format_verdict`, before `apply_verdict`):

```python
def verdict_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Tally events by verdict action (only the four known actions are counted)."""
    counts = {"BLOCK": 0, "WARN": 0, "PASS": 0, "ESCALATE": 0}
    for event in events:
        verdict = event.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def format_topbar(engagement: str, connection_state: str, counts: dict[str, int]) -> Text:
    """Header line: 'Red Robin · <engagement> · ●<connection> · <verdict counts>'.

    16-color-safe: the connection state and each verdict carry a text label + weight,
    so meaning survives non-truecolor terminals and color-blindness."""
    line = Text()
    line.append("Red Robin", style="bold")
    line.append(f"  ·  {engagement or '-'}  ·  ", style=_MUTED)
    line.append("●", style=_STATUS_COLORS.get(connection_state, _MUTED))
    line.append(f" {connection_state}", style=_MUTED)
    for verdict in ("BLOCK", "WARN", "PASS", "ESCALATE"):
        line.append("   ")
        line.append(f"{counts.get(verdict, 0)} {verdict}", style=f"bold {VERDICT_COLORS[verdict]}")
    return line
```

Then add the `TopBar` widget (place it with the other widget classes, e.g. before `SessionsTable`):

```python
class TopBar(Static):
    """Docked header: engagement id + daemon-connection status + verdict counts."""

    def update_status(self, engagement: str, connection_state: str, counts: dict[str, int]) -> None:
        self.update(format_topbar(engagement, connection_state, counts))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -k "verdict_counts or format_topbar or topbar_widget" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py && uv run --no-sync ruff format redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py`
Expected: `All checks passed!` and files formatted.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
git commit -m "feat(tui): add TopBar widget + verdict_counts/format_topbar helpers"
```

---

## Task 2: MemoryPanel out-of-scope in red (panels.py)

**Files:**
- Modify: `redrobin/tui/textual_ui/panels.py`
- Test: `tests/unit/tui_textual/test_panels.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/tui_textual/test_panels.py`:

```python
def test_format_memory_marks_out_of_scope_red():
    out = panels.format_memory(
        {
            "current_phase": "recon",
            "scope_in_scope": ["EC2AMAZ", "10.100.0.0/24"],
            "scope_out_of_scope": ["10.99.0.0/16"],
            "hosts": [{}],
            "credentials": [{}],
            "findings": [{}, {}],
        }
    )
    assert isinstance(out, Text)
    plain = out.plain
    # Text content is preserved (same fields the plain renderer showed).
    assert "recon" in plain
    assert "EC2AMAZ" in plain
    assert "10.99.0.0/16" in plain
    assert "findings 2" in plain
    # The out-of-scope value carries the $scope-out red style.
    assert any(panels._SCOPE_OUT in str(span.style) for span in out.spans)


def test_format_memory_empty_scope_is_graceful():
    out = panels.format_memory({})
    assert "(none)" in out.plain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -k "format_memory" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'format_memory'`.

- [ ] **Step 3: Implement `format_memory` + a `_SCOPE_OUT` constant; switch MemoryPanel to use it**

In `redrobin/tui/textual_ui/panels.py`, add the constant next to `_STATUS_COLORS`:

```python
_SCOPE_OUT = "#f85149"  # mirror $scope-out in theme.tcss
```

Add the pure helper (place it before the `MemoryPanel` class):

```python
def format_memory(snapshot: dict[str, Any]) -> Text:
    """Working-memory summary as Rich Text: phase, scope (out-of-scope in red), counts.
    Same text the plain renderer produced; out-of-scope now styled with $scope-out."""
    in_scope = snapshot.get("scope_in_scope") or []
    out_scope = snapshot.get("scope_out_of_scope") or []
    phase = snapshot.get("current_phase") or "-"
    hosts = len(snapshot.get("hosts") or [])
    creds = len(snapshot.get("credentials") or [])
    finds = len(snapshot.get("findings") or [])
    text = Text()
    text.append(f"phase   {phase}\n\n")
    text.append("SCOPE\n", style=_MUTED)
    text.append(f"  in   {', '.join(in_scope) if in_scope else '(none)'}\n")
    text.append("  out  ")
    text.append(f"{', '.join(out_scope) if out_scope else '(none)'}\n", style=_SCOPE_OUT)
    text.append("\n")
    text.append(f"hosts {hosts}   creds {creds}   findings {finds}")
    return text
```

Replace the body of `MemoryPanel.update_from_snapshot` with:

```python
    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.update(format_memory(snapshot))
```

(Delete the old inline `lines = [...]` / `self.update("\n".join(lines))` block — `format_memory` now owns that rendering.)

- [ ] **Step 4: Run the panel tests to verify they pass (incl. the pre-existing MemoryPanel tests)**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -v`
Expected: PASS — the new `format_memory` tests **and** the pre-existing `test_memory_panel_shows_phase_scope_counts` / `test_memory_panel_empty_scope_is_graceful` (their `str(mem.content)` substring assertions still hold because the plain text is unchanged).

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py && uv run --no-sync ruff format redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
git commit -m "feat(tui): render Working Memory out-of-scope in red ($scope-out)"
```

---

## Task 3: Connection-state reporting in the WS worker (ws_worker.py)

**Files:**
- Modify: `redrobin/tui/textual_ui/ws_worker.py`
- Test: `tests/unit/tui_textual/test_ws_worker.py`

- [ ] **Step 1: Write the failing test**

Add to the top of `tests/unit/tui_textual/test_ws_worker.py` (imports) and append the test:

```python
import asyncio

import pytest
```

```python
@pytest.mark.asyncio
async def test_ws_loop_reports_connecting_then_connected(monkeypatch):
    class _FakeWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # no frames; connection ends immediately

    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()  # one connection, then the generator is exhausted → loop ends

    monkeypatch.setattr(ws_worker, "connect", _fake_connect)
    states: list[str] = []
    await ws_worker.ws_loop(
        target=object(),
        url="ws://x/v1/tui",
        token=None,
        registry=Registry(),
        stop_event=asyncio.Event(),
        set_state=states.append,
    )
    assert states[:2] == ["connecting", "connected"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_ws_worker.py::test_ws_loop_reports_connecting_then_connected -v`
Expected: FAIL — `TypeError: ws_loop() got an unexpected keyword argument 'set_state'`.

- [ ] **Step 3: Add the `set_state` callback to `ws_loop`**

In `redrobin/tui/textual_ui/ws_worker.py`, extend the import:

```python
from typing import Any, Callable
```

Change the `ws_loop` signature + body to emit state at the transitions:

```python
async def ws_loop(
    *,
    target: Any,
    url: str,
    token: str | None,
    registry: Registry,
    stop_event: asyncio.Event,
    set_state: Callable[[str], None] | None = None,
) -> None:
    """Auto-reconnecting consumer. ``target`` is the RedRobinApp; ``registry``
    routes frames to handlers that set its reactive state. ``set_state`` (optional)
    receives ``"connecting"``/``"connected"`` as the socket transitions so the UI
    can show daemon-connection status."""
    full = build_ws_url(url, token)
    if set_state:
        set_state("connecting")
    async for ws in connect(full, ping_interval=20, ping_timeout=20):
        if set_state:
            set_state("connected")
        try:
            async for raw in ws:
                if stop_event.is_set():
                    return
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                handle_raw(target=target, raw=raw, registry=registry)
        except Exception as exc:
            logger.warning("ws_worker reconnecting after error: %s", exc)
            if set_state:
                set_state("connecting")
            if stop_event.is_set():
                return
            continue
        if stop_event.is_set():
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_ws_worker.py -v`
Expected: PASS (the new test + the 4 pre-existing ones).

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/ws_worker.py tests/unit/tui_textual/test_ws_worker.py && uv run --no-sync ruff format redrobin/tui/textual_ui/ws_worker.py tests/unit/tui_textual/test_ws_worker.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/ws_worker.py tests/unit/tui_textual/test_ws_worker.py
git commit -m "feat(tui): ws_loop reports connection state via set_state callback"
```

---

## Task 4: Wire TopBar + connection_state into the app (app.py)

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_app_phase3.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/tui_textual/test_app_phase3.py`:

```python
import pytest

from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp
from redrobin.tui.textual_ui.panels import TopBar


@pytest.mark.asyncio
async def test_topbar_shows_engagement_and_verdict_counts():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {"engagement_id": "e2e-verify", "sessions": [], "findings": []},
            },
        )
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a"},
            },
        )
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {"verdict": "PASS", "command": "whoami", "source": "layer_a"},
            },
        )
        content = str(app.query_one("#topbar", TopBar).content)
        assert "e2e-verify" in content
        assert "1 BLOCK" in content
        assert "1 PASS" in content


@pytest.mark.asyncio
async def test_topbar_reflects_connection_state():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        app.connection_state = "connected"
        await pilot.pause()
        assert "connected" in str(app.query_one("#topbar", TopBar).content)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_app_phase3.py -v`
Expected: FAIL — `query_one("#topbar", TopBar)` raises (topbar is still a plain `Static`) and/or `app.connection_state` does not exist.

- [ ] **Step 3: Wire the TopBar + connection_state reactive into `app.py`**

In `redrobin/tui/textual_ui/app.py`:

(a) Update the panels import. Replace the current single line:

```python
from redrobin.tui.textual_ui.panels import EventsLog, MemoryPanel, SessionsTable, apply_verdict
```

with (adds `TopBar` + `verdict_counts`; `ruff format` in Step 5 normalizes the wrapping):

```python
from redrobin.tui.textual_ui.panels import (
    EventsLog,
    MemoryPanel,
    SessionsTable,
    TopBar,
    apply_verdict,
    verdict_counts,
)
```

(b) Add the reactive next to the existing ones:

```python
    snapshot: reactive[dict] = reactive(dict)
    events: reactive[list] = reactive(list)
    connection_state: reactive[str] = reactive("disconnected")
```

(c) In `compose`, replace the topbar line:

```python
        yield Static("Red Robin", id="topbar")
```

with:

```python
        yield TopBar("Red Robin", id="topbar")
```

(d) Add `_refresh_topbar` and the connection-state watcher/setter (place after `watch_events`):

```python
    def _refresh_topbar(self) -> None:
        """Re-render the header from current snapshot + connection state + verdict counts."""
        try:
            bar = self.query_one("#topbar", TopBar)
        except (NoMatches, ScreenStackError):
            return
        engagement = self.snapshot.get("engagement_id") or "-"
        bar.update_status(engagement, self.connection_state, verdict_counts(self.events))

    def watch_connection_state(self, state: str) -> None:
        self._refresh_topbar()

    def _set_connection_state(self, state: str) -> None:
        """Callback handed to the WS worker; runs on the app loop → safe reactive set."""
        self.connection_state = state
```

(e) In `watch_snapshot`, replace the old topbar update with `_refresh_topbar()` (keep the sessions/memory updates):

```python
    def watch_snapshot(self, snapshot: dict) -> None:
        """Re-render topbar + Sessions + Working Memory on snapshot change. No
        manual repaint — reactive assignment drives Textual's compositor (#155)."""
        self._refresh_topbar()
        try:
            self.query_one("#sessions-table", SessionsTable).update_from_snapshot(snapshot)
            self.query_one("#memory-panel", MemoryPanel).update_from_snapshot(snapshot)
        except (NoMatches, ScreenStackError):
            pass  # pre-mount (no screen yet) or widgets not mounted — compose seeds them
```

(f) In `watch_events`, add the topbar refresh (counts changed):

```python
    def watch_events(self, events: list) -> None:
        """Re-render the verdict/event log + header counts whenever events change."""
        self._refresh_topbar()
        try:
            self.query_one("#events-log", EventsLog).render_events(events)
        except (NoMatches, ScreenStackError):
            pass
```

(g) In `on_mount`, add ONE line to the existing `ws_loop(...)` call — insert `set_state=self._set_connection_state,` immediately after the `stop_event=self._stop_event,` line. Do NOT rewrite the surrounding `run_worker(...)` block (keep its `exit_on_error=False` and the explanatory comment intact). The `ws_loop(...)` argument list becomes:

```python
            ws_loop(
                target=self,
                url=url,
                token=self._token,
                registry=REGISTRY,
                stop_event=self._stop_event,
                set_state=self._set_connection_state,
            ),
```

- [ ] **Step 4: Run tests to verify they pass (incl. the Phase-1/2 app tests)**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_app_phase3.py tests/unit/tui_textual/test_app_reactive.py tests/unit/tui_textual/test_app_phase2.py tests/unit/tui_textual/test_app_layout.py -v`
Expected: PASS. (`test_app_reactive`'s `"e2e-verify" in str(topbar.content)` still holds — `TopBar` is a `Static` and the engagement is still rendered.)

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_app_phase3.py && uv run --no-sync ruff format redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_app_phase3.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_app_phase3.py
git commit -m "feat(tui): drive TopBar status + verdict counts from reactive state"
```

---

## Task 5: Responsive resize hint + focus accent (app.py + theme.tcss)

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`
- Modify: `redrobin/tui/textual_ui/theme.tcss`
- Test: `tests/unit/tui_textual/test_app_phase3.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tui_textual/test_app_phase3.py`:

```python
def test_is_too_small_boundaries():
    from redrobin.tui.textual_ui.app import is_too_small

    assert is_too_small(79, 24) is True
    assert is_too_small(80, 23) is True
    assert is_too_small(80, 24) is False
    assert is_too_small(120, 40) is False


@pytest.mark.asyncio
async def test_resize_hint_shows_when_too_small():
    app = RedRobinApp(start_worker=False)
    async with app.run_test(size=(40, 20)):
        assert app.query_one("#resize-hint").display is True
        assert app.query_one("#main").display is False


@pytest.mark.asyncio
async def test_resize_hint_hidden_when_big_enough():
    app = RedRobinApp(start_worker=False)
    async with app.run_test(size=(100, 30)):
        assert app.query_one("#resize-hint").display is False
        assert app.query_one("#main").display is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_app_phase3.py -k "too_small or resize_hint" -v`
Expected: FAIL — `ImportError: cannot import name 'is_too_small'` / `query_one("#resize-hint")` raises `NoMatches`.

- [ ] **Step 3: Add the responsive policy + hint to `app.py`**

In `redrobin/tui/textual_ui/app.py`:

(a) Add the `Resize` import (with the other textual imports):

```python
from textual.events import Resize
```

(b) Add the module-level pure policy (place it near the top, after `REGISTRY = Registry()`):

```python
def is_too_small(width: int, height: int) -> bool:
    """The dashboard needs at least 80x24 to lay out the three panes legibly."""
    return width < 80 or height < 24
```

(c) In `compose`, add the hint after the advisor dock, before the `Footer`:

```python
        yield Container(id="advisor-dock")  # reserved seam (hidden via .tcss)
        yield Static("Terminal too small — resize to at least 80x24.", id="resize-hint")
        yield Footer()
```

(d) Add the resize handlers (place after `on_mount`/`on_unmount`):

```python
    def on_resize(self, event: Resize) -> None:
        self._apply_responsive(event.size.width, event.size.height)

    def _apply_responsive(self, width: int, height: int) -> None:
        """Below 80x24 the panes can't lay out — show a hint and hide the dashboard."""
        too_small = is_too_small(width, height)
        try:
            self.query_one("#resize-hint").display = too_small
            self.query_one("#main").display = not too_small
        except (NoMatches, ScreenStackError):
            pass
```

- [ ] **Step 4: Add the styles to `theme.tcss`**

Append to `redrobin/tui/textual_ui/theme.tcss`:

```css
/* Below the 80x24 minimum: a centered hint replaces the dashboard (toggled in code). */
#resize-hint {
    display: none;
    width: 100%;
    height: 100%;
    content-align: center middle;
    color: $status-warn;
    text-style: bold;
}

/* Focus affordance: the focused pane gets the accent border (accent = interaction only). */
#main > Container:focus-within {
    border: round $accent;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_app_phase3.py -v`
Expected: PASS (all Phase-3 app tests — topbar, connection, boundaries, resize hint show/hide).

- [ ] **Step 6: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/app.py && uv run --no-sync ruff format redrobin/tui/textual_ui/app.py`
Expected: clean. *(`.tcss` is not linted by ruff.)*

- [ ] **Step 7: Commit**

```bash
git add redrobin/tui/textual_ui/app.py redrobin/tui/textual_ui/theme.tcss
git commit -m "feat(tui): responsive 80x24 hint + focus accent border"
```

---

## Task 6: Full-suite gate + docs

**Files:**
- Modify: `docs/superpowers/TODO.md`

- [ ] **Step 1: Run the exact CI gates locally**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
```
Expected: ruff clean; format reports all files already formatted; import-linter `4 kept, 0 broken`; pytest all green (Phase-3 adds ~12 tests on top of the current 510). If `ruff format --check .` flags anything, run `uv run --no-sync ruff format .` and re-run the gates.

- [ ] **Step 2: Mark Phase 3 done in the roadmap**

In `docs/superpowers/TODO.md`, change the Phase 3 line from `- [ ]` to `- [x]` with a one-line outcome:

```markdown
- [x] **Phase 3 — color + chrome** ✅ — TopBar (connection dot + label + live verdict counts), out-of-scope in red, focus accent (`:focus-within`), responsive 80×24 hint; all 16-color-safe and driven by the reactive store. *(branch `tui/phase-3-color-chrome`; N new tests; M suite green.)*
```

(Fill `N`/`M` with the actual counts from Step 1.)

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/TODO.md
git commit -m "docs: mark TUI Phase 3 (color + chrome) complete"
```

---

## Self-Review (completed while writing)

**1. Spec coverage (S2 + S1 chrome, per TODO Phase 3):**
- `$verdict-*` tokens applied → verdict counts in TopBar use `VERDICT_COLORS` (Task 1); verdict lines already colored (Phase 2). ✓
- TopBar status (connection ● + verdict counts) → Tasks 1 (render) + 3 (worker state) + 4 (wiring). ✓
- Footer → already present and docked (Phase 1 `yield Footer()`); Phase 3 adds no new bindings (Inspector/Palette/Sort actions arrive in Phases 4–5), so the Footer is left as-is rather than faking bindings whose actions don't exist. *(Intentional scope decision; noted so it isn't read as a gap.)*
- 16-color fallback → every verdict/status token carries a text label + weight; asserted in `test_format_topbar_is_16color_safe_text` (Task 1). ✓
- Responsive min-80×24 → `is_too_small` + `#resize-hint` + `on_resize` (Task 5). ✓
- Out-of-scope in red (`$scope-out`, S1/S2) → `format_memory` (Task 2). ✓
- Focus accent (`:focus-within`, S1) → `theme.tcss` (Task 5). ✓

**2. Placeholder scan:** none — every step has the concrete code/command + expected result.

**3. Type/name consistency:** `verdict_counts`, `format_topbar`, `format_memory`, `TopBar.update_status`, `_STATUS_COLORS`, `_SCOPE_OUT`, `connection_state`, `_refresh_topbar`, `_set_connection_state`, `is_too_small`, `_apply_responsive`, `on_resize` — used identically across tasks. `set_state` callback signature `Callable[[str], None]` matches `_set_connection_state(self, state: str)`. `TopBar(Static)` preserves `#topbar` id + `.content` so the Phase-1/2 tests (`test_app_reactive`, `test_app_layout`) keep passing.

---

## Execution Notes

- **Operator smoke (after Task 6, before PR):** start the daemon serving a live engagement, then `python -m redrobin.tui.textual_ui.app` (PowerShell: `$env:REDROBIN_DAEMON_WS="ws://127.0.0.1:7777/v1/tui"; uv run --no-sync python -m redrobin.tui.textual_ui.app`). Expect: TopBar dot turns **green "connected"** once attached; verdict counts tick as gated commands flow (e.g. a blocked `mimikatz` bumps `BLOCK`); Working Memory shows out-of-scope in red; shrinking the terminal below 80×24 shows the resize hint and restores on enlarge.
- **PR:** `tui/phase-3-color-chrome` → `dev-juan` (CI runs on the merge push to `dev-juan`). Then Phase 4 (Inspector `ModalScreen`).
