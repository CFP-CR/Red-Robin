# TUI Textual Phase 2 — Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the three placeholder panes (Sessions / Verdicts·Events / Working Memory) of the Phase-1 Textual app with real widgets bound to the reactive store — Sessions as a `DataTable`, verdicts/events as a color-coded scrollable `RichLog` (with Layer-B in-place correction; fixes #148), and a Working Memory summary.

**Architecture:** A new `redrobin/tui/textual_ui/panels.py` holds three widget subclasses (`SessionsTable(DataTable)`, `EventsLog(RichLog)`, `MemoryPanel(Static)`) plus two PURE helpers (`format_verdict`, `apply_verdict`) so render/correlation logic is unit-testable without a running app — porting the proven logic from the legacy `redrobin/tui/app.py:_format_event_line` + `redrobin/tui/state.py:update_verdict`. `app.py` mounts the widgets and drives them from `watch_snapshot` (Sessions + Memory) and a new `watch_events` (EventsLog); `_on_verdict` routes through `apply_verdict`. Reactive reassignment (not in-place mutation) keeps watchers firing.

**Tech Stack:** Textual `DataTable` / `RichLog` / `Static` widgets, `rich.text.Text` for per-cell/line color, reactive `watch_` methods, Textual `Pilot` (`App.run_test()`) + `pytest-textual-snapshot`.

**Branch:** `tui/phase-2-panels` (off `dev-juan` @ `2cd8e0f`, which has Phase 1 + the CI cleanup). Phase 2 of 6. Spec: `docs/superpowers/specs/2026-05-25-tui-textual-redesign-design.md` §3.1/§3.2.

**Conventions:** atomic commits, **no AI/Claude co-author**. Tests: `uv run --no-sync pytest tests/` (operator daemon holds the venv exe → `--no-sync`). Strict asyncio → keep `@pytest.mark.asyncio` on async Pilot tests. EVERY new/edited file must pass `uv run --no-sync ruff format --check` + `ruff check` (CI gates format). No `# noqa: BLE001` (unused→RUF100). `tui/` is NOT mypy-gated. Daemon/bus/`.cna` untouched; build alongside the legacy client.

---

## File structure

| File | Responsibility |
|---|---|
| `redrobin/tui/textual_ui/panels.py` | **NEW.** `VERDICT_COLORS`; pure `format_verdict(payload)->Text` + `apply_verdict(events, payload)->list`; widgets `SessionsTable(DataTable)`, `EventsLog(RichLog)`, `MemoryPanel(Static)`. |
| `redrobin/tui/textual_ui/app.py` | **MODIFY.** `compose` mounts the 3 widgets (in the existing `#sessions`/`#events`/`#memory` Containers, with border titles); `watch_snapshot` also refreshes Sessions + Memory; new `watch_events` refreshes EventsLog; `_on_verdict` uses `apply_verdict`. |
| `tests/unit/tui_textual/test_panels.py` | **NEW.** Pure-helper unit tests (`format_verdict`, `apply_verdict`) + Pilot widget tests (populate from snapshot/events). |
| `tests/unit/tui_textual/test_app_phase2.py` | **NEW.** Pilot integration: a snapshot frame populates Sessions+Memory; verdict frames populate Events incl. Layer-B in-place correction. |

The legacy `redrobin/tui/*.py` is untouched.

---

## Task 1: Pure render + correlation helpers

**Files:**
- Create: `redrobin/tui/textual_ui/panels.py`
- Test: `tests/unit/tui_textual/test_panels.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tui_textual/test_panels.py`:
```python
from rich.text import Text

from redrobin.tui.textual_ui import panels


def test_format_verdict_is_text_with_command_and_source():
    out = panels.format_verdict(
        {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass"}
    )
    assert isinstance(out, Text)
    plain = out.plain
    assert "BLOCK" in plain
    assert "mimikatz" in plain
    assert "layer_a" in plain
    assert "lsass" in plain


def test_format_verdict_no_reason_omits_dash():
    out = panels.format_verdict({"verdict": "PASS", "command": "whoami", "source": "layer_b"})
    assert "—" not in out.plain


def test_apply_verdict_appends_layer_a():
    events = panels.apply_verdict([], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a"})
    assert len(events) == 1
    assert events[0]["verdict"] == "PASS"


def test_apply_verdict_layer_b_corrects_in_place():
    events = panels.apply_verdict([], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a", "command": "mimikatz"})
    events = panels.apply_verdict(
        events,
        {"correlation_id": "c1", "verdict": "BLOCK", "previous_verdict": "PASS", "source": "layer_b"},
    )
    assert len(events) == 1  # corrected in place, not appended
    assert events[0]["verdict"] == "BLOCK"
    assert events[0]["previous_verdict"] == "PASS"
    assert events[0]["command"] == "mimikatz"  # original fields preserved


def test_apply_verdict_layer_b_no_match_appends():
    events = panels.apply_verdict([], {"correlation_id": "zzz", "verdict": "BLOCK", "source": "layer_b"})
    assert len(events) == 1


def test_apply_verdict_caps_at_500():
    events: list[dict] = []
    for i in range(520):
        events = panels.apply_verdict(events, {"correlation_id": str(i), "verdict": "PASS", "source": "layer_a"})
    assert len(events) == 500
    assert events[-1]["correlation_id"] == "519"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -v`
Expected: FAIL — `ModuleNotFoundError: redrobin.tui.textual_ui.panels`.

- [ ] **Step 3: Implement the helpers**

Create `redrobin/tui/textual_ui/panels.py`:
```python
"""Phase 2 panel widgets + pure render/correlation helpers for the Textual TUI.

``format_verdict`` and ``apply_verdict`` are pure (no running app) so they can
be unit-tested directly — porting the proven logic from the legacy
``redrobin/tui/app.py:_format_event_line`` and ``redrobin/tui/state.py:update_verdict``.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable, RichLog, Static

# Mirror the $verdict-* tokens in theme.tcss. RichLog/DataTable cells take Rich
# styles (not CSS vars), so the concrete hexes live here; keep in sync with theme.tcss.
VERDICT_COLORS: dict[str, str] = {
    "BLOCK": "#f85149",
    "WARN": "#e3b341",
    "PASS": "#3fb950",
    "ESCALATE": "#58a6ff",
}
_SEV_HIGH = "#f0883e"
_MUTED = "#8b949e"
_MAX_EVENTS = 500


def format_verdict(payload: dict[str, Any]) -> Text:
    """One color-coded line: '<VERDICT> <command>  (<source>) — <reason>'.
    No raw JSON (fixes #148)."""
    verdict = str(payload.get("verdict", "?"))
    cmd = str(payload.get("command", ""))
    source = str(payload.get("source", "?"))
    reason = str(payload.get("reason", ""))
    color = VERDICT_COLORS.get(verdict, "white")
    line = Text()
    line.append(f"{verdict:<8}", style=f"bold {color}")
    line.append(f" {cmd}  ")
    line.append(f"({source})", style=_MUTED)
    if reason:
        line.append(f" — {reason}", style=_MUTED)
    return line


def apply_verdict(
    events: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return a NEW events list with the verdict applied. A Layer B correction
    (``source == "layer_b"`` with a matching ``correlation_id``) updates the most
    recent matching entry in place; otherwise the verdict is appended. Capped at
    the last ``_MAX_EVENTS``. Reassignment (not mutation) so the reactive watcher
    fires. Ports ``redrobin/tui/state.py:update_verdict``."""
    new = list(events)
    source = payload.get("source", "layer_a")
    corr = payload.get("correlation_id")
    if source == "layer_b" and corr is not None:
        for i in range(len(new) - 1, -1, -1):
            if new[i].get("correlation_id") == corr:
                new[i] = {**new[i], **payload}
                return new[-_MAX_EVENTS:]
    new.append(payload)
    return new[-_MAX_EVENTS:]


class SessionsTable(DataTable):
    """Active-beacon table: BID / HOST / USER / INT."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("BID", "HOST", "USER", "INT")

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not self.columns:  # not yet mounted / columns not added
            return
        self.clear()
        for s in snapshot.get("sessions") or []:
            integ = str(s.get("integrity") or "-")
            integ_cell = Text(integ, style=_SEV_HIGH) if integ == "High" else Text(integ)
            self.add_row(
                str(s.get("beacon_id") or "?"),
                str(s.get("hostname") or "-"),
                str(s.get("user") or "-"),
                integ_cell,
            )


class EventsLog(RichLog):
    """Scrollable, color-coded verdict/event log. Re-rendered wholesale from the
    events list (so Layer-B in-place corrections appear correctly)."""

    def render_events(self, events: list[dict[str, Any]]) -> None:
        self.clear()
        for payload in events:
            self.write(format_verdict(payload))


class MemoryPanel(Static):
    """Working-memory summary: phase, scope (in/out), and counts."""

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        in_scope = snapshot.get("scope_in_scope") or []
        out_scope = snapshot.get("scope_out_of_scope") or []
        phase = snapshot.get("current_phase") or "-"
        hosts = len(snapshot.get("hosts") or [])
        creds = len(snapshot.get("credentials") or [])
        finds = len(snapshot.get("findings") or [])
        lines = [
            f"phase   {phase}",
            "",
            "SCOPE",
            f"  in   {', '.join(in_scope) if in_scope else '(none)'}",
            f"  out  {', '.join(out_scope) if out_scope else '(none)'}",
            "",
            f"hosts {hosts}   creds {creds}   findings {finds}",
        ]
        self.update("\n".join(lines))
```

- [ ] **Step 4: Run to verify it PASSES**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -v`
Expected: 6 passed.

- [ ] **Step 5: Format + lint**

Run:
```
uv run --no-sync ruff format redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
uv run --no-sync ruff format --check redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
uv run --no-sync ruff check redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
```
Expected: formatted + ruff check passes. Fix any lint; re-run.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_panels.py
git commit -m "feat(tui): panel render helpers + widget classes (format_verdict, apply_verdict, Sessions/Events/Memory)"
```

---

## Task 2: SessionsTable populates from a snapshot (Pilot)

**Files:**
- Modify: `tests/unit/tui_textual/test_panels.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/tui_textual/test_panels.py`:
```python
import pytest
from textual.app import App, ComposeResult

from redrobin.tui.textual_ui.panels import SessionsTable


class _SessApp(App):
    def compose(self) -> ComposeResult:
        yield SessionsTable(id="t")


@pytest.mark.asyncio
async def test_sessions_table_populates_rows():
    app = _SessApp()
    async with app.run_test():
        table = app.query_one("#t", SessionsTable)
        table.update_from_snapshot(
            {
                "sessions": [
                    {"beacon_id": "2047462324", "hostname": "EC2AMAZ", "user": "Administrator", "integrity": "High"},
                    {"beacon_id": "1200206136", "hostname": "JCSP", "user": "jcspi", "integrity": "Medium"},
                ]
            }
        )
        assert table.row_count == 2
        assert str(table.get_cell_at((0, 0))) == "2047462324"
        assert str(table.get_cell_at((0, 3))) == "High"


@pytest.mark.asyncio
async def test_sessions_table_clears_on_reupdate():
    app = _SessApp()
    async with app.run_test():
        table = app.query_one("#t", SessionsTable)
        table.update_from_snapshot({"sessions": [{"beacon_id": "a", "hostname": "h", "user": "u", "integrity": "Low"}]})
        table.update_from_snapshot({"sessions": []})
        assert table.row_count == 0
```

- [ ] **Step 2: Run to verify it PASSES**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -k sessions_table -v`
Expected: 2 passed. (Helpers + widget already exist from Task 1; these prove the widget works mounted.)
- If `get_cell_at((0, 3))` returns a `Text`, `str(...)` yields its plain text — assertion holds. If the API differs, confirm against Textual docs (DataTable `get_cell_at(Coordinate)` / `row_count`) and adjust the accessor only.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tui_textual/test_panels.py
git commit -m "test(tui): SessionsTable populates + clears from snapshot (Pilot)"
```

---

## Task 3: EventsLog renders + MemoryPanel summarizes (Pilot)

**Files:**
- Modify: `tests/unit/tui_textual/test_panels.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/tui_textual/test_panels.py`:
```python
from redrobin.tui.textual_ui.panels import EventsLog, MemoryPanel


class _LogApp(App):
    def compose(self) -> ComposeResult:
        yield EventsLog(id="log")


class _MemApp(App):
    def compose(self) -> ComposeResult:
        yield MemoryPanel(id="mem")


@pytest.mark.asyncio
async def test_events_log_writes_one_line_per_event():
    app = _LogApp()
    async with app.run_test():
        log = app.query_one("#log", EventsLog)
        log.render_events(
            [
                {"verdict": "PASS", "command": "whoami", "source": "layer_a"},
                {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass"},
            ]
        )
        # RichLog stores written renderables in .lines; 2 events → 2 lines.
        assert len(log.lines) == 2


@pytest.mark.asyncio
async def test_memory_panel_shows_phase_scope_counts():
    app = _MemApp()
    async with app.run_test():
        mem = app.query_one("#mem", MemoryPanel)
        mem.update_from_snapshot(
            {
                "current_phase": "recon",
                "scope_in_scope": ["EC2AMAZ", "10.100.0.0/24"],
                "scope_out_of_scope": ["10.99.0.0/16"],
                "hosts": [{}],
                "credentials": [{}],
                "findings": [{}, {}],
            }
        )
        content = str(mem.content)
        assert "recon" in content
        assert "EC2AMAZ" in content
        assert "10.99.0.0/16" in content
        assert "findings 2" in content


@pytest.mark.asyncio
async def test_memory_panel_empty_scope_is_graceful():
    app = _MemApp()
    async with app.run_test():
        mem = app.query_one("#mem", MemoryPanel)
        mem.update_from_snapshot({})
        content = str(mem.content)
        assert "(none)" in content
```

- [ ] **Step 2: Run to verify it PASSES**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py -k "events_log or memory_panel" -v`
Expected: 3 passed.
- `RichLog.lines` is the rendered-strip list; one `write()` per event yields one entry per (single-line) event. If a `Text` line wraps, `len(lines)` could exceed the event count — these test lines are short and won't wrap at the default 80-col test size. If the API differs, confirm `RichLog` exposes `.lines` (or use `log.write` call-count via a spy) and adjust.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/tui_textual/test_panels.py
git commit -m "test(tui): EventsLog line-per-event + MemoryPanel phase/scope/counts (Pilot)"
```

---

## Task 4: Mount the widgets in the app + drive them from the reactive store

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`

- [ ] **Step 1: Replace the placeholder panes in `compose`**

In `redrobin/tui/textual_ui/app.py`, add to the imports (with the other local imports):
```python
from redrobin.tui.textual_ui.panels import EventsLog, MemoryPanel, SessionsTable, apply_verdict
```

Replace the `compose` body's `Horizontal(id="main")` block. The current Phase-1 version is:
```python
        with Horizontal(id="main"):
            yield Container(Static("Sessions"), id="sessions")
            yield Container(Static("Verdicts / Events"), id="events")
            yield Container(Static("Working Memory"), id="memory")
```
Replace it with (border titles give each pane its header; the real widgets fill them):
```python
        with Horizontal(id="main"):
            with Container(id="sessions") as sessions_box:
                sessions_box.border_title = "Sessions"
                yield SessionsTable(id="sessions-table")
            with Container(id="events") as events_box:
                events_box.border_title = "Verdicts / Events"
                yield EventsLog(id="events-log", highlight=False, markup=False)
            with Container(id="memory") as memory_box:
                memory_box.border_title = "Working Memory"
                yield MemoryPanel(id="memory-panel")
```
(`Static` may now be unused in `app.py` except for the topbar — keep the `Static` import; the topbar still uses it.)

- [ ] **Step 2: Extend `watch_snapshot` to refresh Sessions + Memory**

The current Phase-1 `watch_snapshot` updates only the topbar. Replace it with:
```python
    def watch_snapshot(self, snapshot: dict) -> None:
        """Re-render topbar + Sessions + Working Memory on snapshot change. No
        manual repaint — reactive assignment drives Textual's compositor (#155)."""
        engagement = snapshot.get("engagement_id") or "-"
        try:
            self.query_one("#topbar", Static).update(f"Red Robin · {engagement} · ●")
            self.query_one("#sessions-table", SessionsTable).update_from_snapshot(snapshot)
            self.query_one("#memory-panel", MemoryPanel).update_from_snapshot(snapshot)
        except (NoMatches, ScreenStackError):
            pass  # pre-mount (no screen yet) or widgets not mounted — compose seeds them
```

- [ ] **Step 3: Add `watch_events` to refresh the EventsLog**

Add this method to `RedRobinApp` (right after `watch_snapshot`):
```python
    def watch_events(self, events: list) -> None:
        """Re-render the verdict/event log whenever the events list changes."""
        try:
            self.query_one("#events-log", EventsLog).render_events(events)
        except (NoMatches, ScreenStackError):
            pass
```

- [ ] **Step 4: Route `_on_verdict` through `apply_verdict` (Layer-B correction)**

Replace the Phase-1 module-level `_on_verdict` handler:
```python
@REGISTRY.register("opsec.verdict")
def _on_verdict(app: "RedRobinApp", payload: dict) -> None:
    # Reassign (not append) so watch_events fires; apply_verdict handles the
    # Layer B in-place correction by correlation_id.
    app.events = apply_verdict(app.events, payload)
```

- [ ] **Step 5: Format + lint + verify Phase-1 tests still pass**

Run:
```
uv run --no-sync ruff format redrobin/tui/textual_ui/app.py
uv run --no-sync ruff format --check redrobin/tui/textual_ui/app.py
uv run --no-sync ruff check redrobin/tui/textual_ui/app.py
uv run --no-sync pytest tests/unit/tui_textual/ -q
```
Expected: ruff clean; the existing Phase-1 tests (layout, reactive, frames, ws_worker) + Task 1-3 panel tests all pass. The Phase-1 `test_app_layout.py` asserts `#sessions`/`#events`/`#memory` exist — still true (they're the Container ids). `test_app_reactive.py` (`test_snapshot_frame_updates_state_and_topbar`) still passes (topbar still updated). If `test_app_reactive.py::test_verdict_frame_appends_event` asserted exact append behavior, confirm it still holds (a single layer_a verdict still appends one event) — it does.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/app.py
git commit -m "feat(tui): mount Sessions/Events/Memory widgets + drive from reactive store"
```

---

## Task 5: Integration — frames populate the panels (Pilot)

**Files:**
- Create: `tests/unit/tui_textual/test_app_phase2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tui_textual/test_app_phase2.py`:
```python
import pytest

from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp
from redrobin.tui.textual_ui.panels import EventsLog, MemoryPanel, SessionsTable


@pytest.mark.asyncio
async def test_snapshot_frame_populates_sessions_and_memory():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {
                    "engagement_id": "e2e-verify",
                    "current_phase": "recon",
                    "sessions": [{"beacon_id": "b1", "hostname": "H", "user": "u", "integrity": "High"}],
                    "findings": [{}, {}, {}],
                    "credentials": [{}],
                    "hosts": [{}],
                },
            },
        )
        assert app.query_one("#sessions-table", SessionsTable).row_count == 1
        assert "findings 3" in str(app.query_one("#memory-panel", MemoryPanel).content)


@pytest.mark.asyncio
async def test_verdict_frames_render_and_layer_b_corrects_in_place():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={"type": "opsec.verdict", "payload": {"correlation_id": "c1", "verdict": "ESCALATE", "command": "seatbelt", "source": "layer_a"}},
        )
        REGISTRY.dispatch(
            target=app,
            frame={"type": "opsec.verdict", "payload": {"correlation_id": "c1", "verdict": "PASS", "previous_verdict": "ESCALATE", "source": "layer_b"}},
        )
        # Corrected in place → still one event, now PASS.
        assert len(app.events) == 1
        assert app.events[0]["verdict"] == "PASS"
        # And the log rendered one line.
        assert len(app.query_one("#events-log", EventsLog).lines) == 1
```

- [ ] **Step 2: Run to verify it PASSES**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_app_phase2.py -v`
Expected: 2 passed. (Wiring from Task 4 makes these green; if red-first is desired, run before Task 4 lands.)

- [ ] **Step 3: Format + lint + commit**

```
uv run --no-sync ruff format tests/unit/tui_textual/test_app_phase2.py
uv run --no-sync ruff format --check tests/unit/tui_textual/test_app_phase2.py
uv run --no-sync ruff check tests/unit/tui_textual/test_app_phase2.py
```
Then:
```bash
git add tests/unit/tui_textual/test_app_phase2.py
git commit -m "test(tui): frames populate Sessions/Memory + Layer-B in-place verdict correction"
```

---

## Task 6: Visual snapshot + full-suite gate + TODO note

**Files:**
- Create: `tests/unit/tui_textual/test_phase2_snapshot.py`
- Modify: `docs/superpowers/TODO.md`

- [ ] **Step 1: Write a Textual SVG snapshot test of the populated dashboard**

Create `tests/unit/tui_textual/test_phase2_snapshot.py`:
```python
import pytest

from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp


@pytest.mark.asyncio
async def test_dashboard_snapshot(snap_compare):
    """SVG snapshot of the dashboard with sample data. On first run this
    creates the baseline; later runs diff against it (catches visual regressions)."""
    app = RedRobinApp(start_worker=False)

    async def _seed(pilot):
        REGISTRY.dispatch(
            target=pilot.app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {
                    "engagement_id": "e2e-verify",
                    "current_phase": "recon",
                    "sessions": [{"beacon_id": "2047462324", "hostname": "EC2AMAZ", "user": "Administrator", "integrity": "High"}],
                    "findings": [{}], "credentials": [{}], "hosts": [{}],
                },
            },
        )
        REGISTRY.dispatch(
            target=pilot.app,
            frame={"type": "opsec.verdict", "payload": {"correlation_id": "c1", "verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass touch"}},
        )

    assert snap_compare(app, run_before=_seed)
```

- [ ] **Step 2: Generate the snapshot baseline + run it**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_phase2_snapshot.py --snapshot-update -q`
Expected: passes and writes a baseline under `tests/unit/tui_textual/__snapshots__/`. Then run WITHOUT the flag to confirm it diffs clean:
`uv run --no-sync pytest tests/unit/tui_textual/test_phase2_snapshot.py -q` → 1 passed.
- `snap_compare` is the `pytest-textual-snapshot` fixture (already a dev dep). If the fixture name/signature differs in the installed version, confirm via its docs and adjust (`run_before` drives the app before the snapshot).

- [ ] **Step 3: Full-suite gate**

Run: `uv run --no-sync pytest tests/ -q`
Expected: all green (Phase-1 + Phase-2 tests + the rest of the suite; the new snapshot/baseline committed). No regressions in the untouched daemon/legacy code.

- [ ] **Step 4: Note progress in TODO.md**

In `docs/superpowers/TODO.md`, under the "TUI → Textual redesign" section, change the Phase 2 line from `- [ ] **Phase 2 — panels** …` to `- [x] **Phase 2 — panels** ✅ — Sessions DataTable, Verdicts/Events RichLog (color-coded, Layer-B in-place by correlation_id, fixes #148), Working Memory; bound to the reactive store.`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/tui_textual/test_phase2_snapshot.py tests/unit/tui_textual/__snapshots__ docs/superpowers/TODO.md
git commit -m "test(tui): dashboard SVG snapshot; mark Phase 2 (panels) complete"
```

---

## Self-review

**Spec coverage (§3.1 panels / §3.2 color, Phase-2 slice):**
- Sessions `DataTable` (BID/HOST/USER/INT, integrity color) → Task 1 (`SessionsTable`) + Task 2. ✅
- Verdicts/Events `RichLog`, color-coded, **no raw JSON (#148)** → Task 1 (`format_verdict`) + Task 3. ✅
- **Layer-B in-place correction by correlation_id** → Task 1 (`apply_verdict`) + Task 4 (`_on_verdict`) + Task 5. ✅
- Working Memory (scope/phase/counts, graceful empty) → Task 1 (`MemoryPanel`) + Task 3. ✅
- Bound to the reactive store (`watch_snapshot` + new `watch_events`) → Task 4 + Task 5. ✅
- Color tokens applied via `VERDICT_COLORS`/`_SEV_HIGH` (mirroring theme.tcss) → Task 1. ✅
- Visual regression coverage → Task 6 (snapshot). ✅
- #156 (scope seeding) NOT in scope — MemoryPanel renders `(none)` gracefully until Phase 6. ✅

**Placeholder scan:** none — every step has complete code + exact commands.

**Type/name consistency:** `format_verdict(payload)->Text`, `apply_verdict(events, payload)->list`, `SessionsTable.update_from_snapshot`, `EventsLog.render_events`, `MemoryPanel.update_from_snapshot`, widget ids `#sessions-table`/`#events-log`/`#memory-panel`, `_on_verdict` via `apply_verdict` — used consistently across Tasks 1–6. The `watch_snapshot`/`watch_events` guards reuse the Phase-1 `NoMatches, ScreenStackError` import already in `app.py`.

**API-verification note for `/auditing-plans`:** confirm against real Textual 8.2.7 — `DataTable.add_columns`/`add_row`(Rich `Text` cell)/`clear`/`row_count`/`get_cell_at(Coordinate)`/`columns`/`zebra_stripes`/`cursor_type`; `RichLog(highlight=, markup=)`/`write`/`clear`/`.lines`; `Static.content`/`update`; `Container.border_title`; `with Container() as x:` attribute-set in `compose`; and the `pytest-textual-snapshot` `snap_compare(app, run_before=...)` fixture signature. These are the highest-risk items (Phase-1's audit caught `Static.renderable`→`.content`).

---

## Next
After `/auditing-plans` applies fixes → `/subagent-driven-development` (Opus 4.7) → operator smoke (run `python -m redrobin.tui.textual_ui.app` against a live daemon with sessions/verdicts; confirm the panels populate live). Then Phase 3 (color + chrome: TopBar status dot + verdict counts, Footer polish, 16-color fallback, responsive).
