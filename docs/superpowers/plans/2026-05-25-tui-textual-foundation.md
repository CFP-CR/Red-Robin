# TUI Textual Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the new Textual TUI client *alongside* the existing prompt_toolkit one — an app shell, centralized theme tokens, a WS worker, a reactive state store, and a frame-routing registry — proving live frames update the UI with no manual repaint (#155 dissolves) before any panel detail is built.

**Architecture:** A new `redrobin/tui/textual_ui/` package. A `RedRobinApp(textual.App)` holds reactive `snapshot`/`events`; a generic frame **registry** (`{type: handler}`) routes WS frames to handlers that set those reactives; Textual's reactive system re-renders bound widgets automatically. A worker connects to the unchanged `/v1/tui` WS and feeds frames through the registry. The layout reserves a hidden `#advisor-dock` region and uses the registry + provider patterns as the extensibility seams for future features (the AI Advisor). The daemon is untouched.

**Tech Stack:** Python, [Textual](https://textual.textualize.io/) (App/reactive/workers/widgets/CSS), `websockets` (already a dep, used by the old client), `pytest` + Textual `Pilot` (`App.run_test()`) + `pytest-textual-snapshot`, `uv` for deps.

**Spec:** `docs/superpowers/specs/2026-05-25-tui-textual-redesign-design.md` (this is Plan 1 of the 6-phase breakdown in §2).

**Conventions:** atomic commits, **no AI/Claude co-author trailer**. Run tests with `uv run pytest tests/` (bare `uv run pytest` mis-collects in this repo). Daemon/bus/reducer/`.cna` untouched.

---

## File structure

| File | Responsibility |
|---|---|
| `redrobin/tui/textual_ui/__init__.py` | Package marker. |
| `redrobin/tui/textual_ui/frames.py` | Generic frame-routing **registry** (`register` / `dispatch` / `has_handler`). The #1 extensibility seam — new frame types register here without touching existing routing. |
| `redrobin/tui/textual_ui/app.py` | `RedRobinApp(App)` — reactive `snapshot`/`events`, `compose()` (TopBar + 3 panes + reserved `#advisor-dock` + Footer), concrete frame handlers, `watch_snapshot`, `on_mount` (starts the worker), `main()` (`python -m` entry). |
| `redrobin/tui/textual_ui/theme.tcss` | Centralized semantic color tokens + layout rules. |
| `redrobin/tui/textual_ui/ws_worker.py` | `build_ws_url`, pure `handle_raw(app, raw)` (json→dispatch), async `ws_loop(app, url, token, stop_event)` (connect + read). |
| `tests/unit/tui_textual/test_frames.py` | Registry behavior + **extensibility acceptance**. |
| `tests/unit/tui_textual/test_app_layout.py` | Pilot: named regions exist; `#advisor-dock` hidden; Footer present. |
| `tests/unit/tui_textual/test_app_reactive.py` | Pilot: a snapshot frame updates `app.snapshot` + the TopBar (the reactive-render proof). |
| `tests/unit/tui_textual/test_ws_worker.py` | `handle_raw` dispatches valid frames; ignores bad JSON / unknown types. |
| `tests/integration/test_textual_ws_contract.py` | Daemon `TestClient`: the frame types the daemon emits are exactly the types the client registry handles. |

The existing `redrobin/tui/*.py` (prompt_toolkit) is left untouched until Phase 6 swaps the entrypoint.

---

## Task 1: Add Textual dependencies

**Files:**
- Modify: `pyproject.toml` (+ `uv.lock`) via `uv add`

- [ ] **Step 1: Add the runtime + test deps**

Run:
```bash
cd /c/Users/jcspi/Documents/dev-projects/Red-Robin
uv add textual
uv add websockets
uv add --group dev pytest-textual-snapshot
```
Expected: resolves and installs; `pyproject.toml` gains `textual` + `websockets` under `[project] dependencies` and `pytest-textual-snapshot` under the dev group; `uv.lock` updates.

> **Audit notes:** `websockets` was previously only a *transitive* dep of `uvicorn` (16.0) — `ws_worker.py` imports it directly, so we declare it honestly. `pytest-asyncio>=0.23` is **already** a dev dep and the repo runs pytest in **strict** asyncio mode, so every async Pilot test below keeps its explicit `@pytest.mark.asyncio` marker — **no global pytest-config change.**

- [ ] **Step 2: Verify the import works**

Run: `uv run python -c "import textual; from textual.app import App; print(textual.__version__)"`
Expected: prints a version (e.g. `0.x`/`1.x`) with no ImportError.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add textual + pytest-textual-snapshot deps"
```

---

## Task 2: Frame-routing registry (extensibility seam #1)

**Files:**
- Create: `redrobin/tui/textual_ui/__init__.py`
- Create: `redrobin/tui/textual_ui/frames.py`
- Test: `tests/unit/tui_textual/test_frames.py`

- [ ] **Step 1: Create the package marker**

Create `redrobin/tui/textual_ui/__init__.py`:
```python
"""Textual TUI client (Phase 1 foundation). Built alongside the legacy
prompt_toolkit client in ``redrobin/tui/``; the entrypoint swaps in Phase 6."""
```

Also create the **empty** test package marker (this repo packages its test dirs — every sibling under `tests/unit/` has an `__init__.py`, and there is no `conftest.py` to normalize import modes, so the marker is required for clean collection):
```bash
mkdir -p tests/unit/tui_textual && : > tests/unit/tui_textual/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/tui_textual/test_frames.py`:
```python
from redrobin.tui.textual_ui import frames


def test_register_and_dispatch_calls_handler():
    seen = {}
    reg = frames.Registry()

    @reg.register("demo.event")
    def _handle(target, payload):
        seen["payload"] = payload

    handled = reg.dispatch(target=object(), frame={"type": "demo.event", "payload": {"a": 1}})

    assert handled is True
    assert seen["payload"] == {"a": 1}


def test_dispatch_unknown_type_is_ignored():
    reg = frames.Registry()
    handled = reg.dispatch(target=object(), frame={"type": "nope", "payload": {}})
    assert handled is False


def test_has_handler():
    reg = frames.Registry()
    reg.register("x")(lambda t, p: None)
    assert reg.has_handler("x") is True
    assert reg.has_handler("y") is False


def test_extensibility_new_type_does_not_disturb_existing():
    """Acceptance for the seam: registering a NEW type leaves existing
    routing untouched and dispatchable."""
    reg = frames.Registry()
    calls = []
    reg.register("working_memory_snapshot")(lambda t, p: calls.append("wm"))
    # A future feature adds its type — no edit to existing handlers/dispatch.
    reg.register("advisor.recommendation")(lambda t, p: calls.append("adv"))

    reg.dispatch(target=None, frame={"type": "working_memory_snapshot", "payload": {}})
    reg.dispatch(target=None, frame={"type": "advisor.recommendation", "payload": {}})

    assert calls == ["wm", "adv"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/tui_textual/test_frames.py -v`
Expected: FAIL — `ModuleNotFoundError: redrobin.tui.textual_ui.frames` (or `AttributeError: Registry`).

- [ ] **Step 4: Write the registry**

Create `redrobin/tui/textual_ui/frames.py`:
```python
"""Generic WS-frame routing registry — the core extensibility seam.

A frame is ``{"type": str, "payload": dict}``. Features register a handler for
their type; ``dispatch`` routes by type. Adding a new frame type (e.g.
``advisor.recommendation``) is a single ``@registry.register(...)`` — it never
requires editing existing handlers or the dispatch logic.
"""
from __future__ import annotations

from typing import Any, Callable

FrameHandler = Callable[[Any, dict], None]


class Registry:
    """Maps a frame ``type`` to a handler ``(target, payload) -> None``."""

    def __init__(self) -> None:
        self._handlers: dict[str, FrameHandler] = {}

    def register(self, frame_type: str) -> Callable[[FrameHandler], FrameHandler]:
        def deco(fn: FrameHandler) -> FrameHandler:
            self._handlers[frame_type] = fn
            return fn

        return deco

    def has_handler(self, frame_type: str) -> bool:
        return frame_type in self._handlers

    def dispatch(self, *, target: Any, frame: dict) -> bool:
        """Route one frame to its handler. Returns True if handled."""
        handler = self._handlers.get(frame.get("type", ""))
        if handler is None:
            return False
        handler(target, frame.get("payload") or {})
        return True


__all__ = ["Registry", "FrameHandler"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/tui_textual/test_frames.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/__init__.py redrobin/tui/textual_ui/frames.py tests/unit/tui_textual/__init__.py tests/unit/tui_textual/test_frames.py
git commit -m "feat(tui): generic frame-routing registry (extensibility seam)"
```

---

## Task 3: Theme tokens (`theme.tcss`)

**Files:**
- Create: `redrobin/tui/textual_ui/theme.tcss`

- [ ] **Step 1: Create the theme file**

Create `redrobin/tui/textual_ui/theme.tcss`:
```css
/* Centralized semantic tokens — referenced by function, not appearance.
   16-color-safe: widgets pair color with text + weight, so meaning survives
   non-truecolor terminals and color-blindness. Accent is reserved for focus. */
$verdict-block: #f85149;
$verdict-warn: #e3b341;
$verdict-pass: #3fb950;
$verdict-escalate: #58a6ff;
$sev-high: #f0883e;
$scope-out: #f85149;
$status-ok: #3fb950;
$status-warn: #e3b341;
$status-err: #f85149;
$accent: #58a6ff;
$text-muted: #8b949e;

Screen {
    layout: vertical;
}

#topbar {
    dock: top;
    height: 1;
    background: $panel;
    color: $text;
}

#main {
    height: 1fr;
}

#main > Container {
    border: round $panel-lighten-2;
    width: 1fr;
}

#sessions { width: 2fr; }
#events { width: 3fr; }
#memory { width: 2fr; }

/* Reserved seam for the future AI Advisor (Phase S6). Present but hidden. */
#advisor-dock {
    display: none;
    dock: bottom;
    height: auto;
    border-top: heavy $accent;
}
```

- [ ] **Step 2: Commit**

```bash
git add redrobin/tui/textual_ui/theme.tcss
git commit -m "feat(tui): semantic color theme tokens (.tcss)"
```

*(No test here — the theme is validated when the App loads it in Task 4's Pilot test; a malformed `.tcss` makes `App.run_test()` raise.)*

---

## Task 4: App shell + layout (named regions + hidden advisor dock)

**Files:**
- Create: `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_app_layout.py`

- [ ] **Step 1: Write the failing layout test**

Create `tests/unit/tui_textual/test_app_layout.py`:
```python
import pytest

from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_layout_has_named_regions():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        # The three panes, the topbar, and the reserved advisor dock all exist.
        for selector in ("#topbar", "#sessions", "#events", "#memory", "#advisor-dock"):
            assert app.query(selector), f"missing region {selector}"
        # Footer (Textual built-in) is present for keybinding discoverability.
        from textual.widgets import Footer
        assert app.query(Footer)


@pytest.mark.asyncio
async def test_advisor_dock_is_hidden():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        dock = app.query_one("#advisor-dock")
        assert dock.display is False  # reserved seam, not shown in Phase 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tui_textual/test_app_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: redrobin.tui.textual_ui.app`.

- [ ] **Step 3: Write the App shell**

Create `redrobin/tui/textual_ui/app.py`:
```python
"""RedRobinApp — the Textual TUI client shell (Phase 1 foundation).

Holds reactive ``snapshot``/``events`` updated by frame handlers, composes the
layout (topbar + three panes + reserved advisor dock + footer), and starts the
WS worker on mount. Panel content is intentionally minimal here — Phase 2 fills
the panes with DataTable/RichLog widgets.
"""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Static

from redrobin.tui.textual_ui.frames import Registry

# Module-level registry: handlers register at import time (Task 5). A future
# feature adds its frame type with a new @REGISTRY.register — no edits here.
REGISTRY = Registry()


class RedRobinApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "Red Robin"
    BINDINGS = [("q", "quit", "Quit")]

    snapshot: reactive[dict] = reactive(dict)
    events: reactive[list] = reactive(list)

    def __init__(self, *, start_worker: bool = True, ws_url: str | None = None,
                 token: str | None = None) -> None:
        super().__init__()
        self._start_worker = start_worker
        self._ws_url = ws_url
        self._token = token

    def compose(self) -> ComposeResult:
        yield Static("Red Robin", id="topbar")
        with Horizontal(id="main"):
            yield Container(Static("Sessions"), id="sessions")
            yield Container(Static("Verdicts / Events"), id="events")
            yield Container(Static("Working Memory"), id="memory")
        yield Container(id="advisor-dock")  # reserved seam (hidden via .tcss)
        yield Footer()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/tui_textual/test_app_layout.py -v`
Expected: 2 passed. (If `test_advisor_dock_is_hidden` fails on `.display`, confirm the `#advisor-dock { display: none; }` rule loaded — a `.tcss` typo raises at `run_test()`.)

- [ ] **Step 5: Commit**

```bash
git add redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_app_layout.py
git commit -m "feat(tui): Textual app shell + layout with reserved advisor-dock seam"
```

---

## Task 5: Reactive snapshot handler + topbar render (the #155 proof, unit level)

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_app_reactive.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tui_textual/test_app_reactive.py`:
```python
import pytest

from redrobin.tui.textual_ui.app import RedRobinApp, REGISTRY


@pytest.mark.asyncio
async def test_snapshot_frame_updates_state_and_topbar():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={"type": "working_memory_snapshot",
                   "payload": {"engagement_id": "e2e-verify", "sessions": [], "findings": []}},
        )
        # No manual repaint call — setting the reactive is enough.
        assert app.snapshot.get("engagement_id") == "e2e-verify"
        from textual.widgets import Static
        topbar = app.query_one("#topbar", Static)
        assert "e2e-verify" in str(topbar.content)  # Static exposes .content (NOT .renderable)


@pytest.mark.asyncio
async def test_verdict_frame_appends_event():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={"type": "opsec.verdict",
                   "payload": {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a"}},
        )
        assert len(app.events) == 1
        assert app.events[0]["verdict"] == "BLOCK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tui_textual/test_app_reactive.py -v`
Expected: FAIL — no handler registered, so `app.snapshot` stays `{}` and the assertions fail (and `topbar.renderable` is the static "Red Robin").

- [ ] **Step 3: Add handlers + the watcher**

Edit `redrobin/tui/textual_ui/app.py`. Append the handlers and the `watch_snapshot` method.

Add to the imports block:
```python
from textual.css.query import NoMatches
```

Add these two registrations at module level, **after** the `class RedRobinApp(...)` definition (so the class is defined when they reference it via the `target`):
```python
@REGISTRY.register("working_memory_snapshot")
def _on_snapshot(app: "RedRobinApp", payload: dict) -> None:
    app.snapshot = payload  # reactive assignment → watch_snapshot fires


@REGISTRY.register("opsec.verdict")
def _on_verdict(app: "RedRobinApp", payload: dict) -> None:
    # Reassign (not append) so the reactive watcher sees the change.
    app.events = app.events + [payload]
```

Add this method **inside** `class RedRobinApp`:
```python
    def watch_snapshot(self, snapshot: dict) -> None:
        """Re-render the topbar when the snapshot changes. No invalidate() —
        Textual repaints bound widgets automatically (this is why #155 cannot
        recur: there is no manual repaint path to miss)."""
        engagement = snapshot.get("engagement_id") or "-"
        try:
            self.query_one("#topbar", Static).update(f"Red Robin · {engagement} · ●")
        except NoMatches:
            pass  # snapshot set before mount; compose() seeds the static text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/tui_textual/test_app_reactive.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_app_reactive.py
git commit -m "feat(tui): reactive snapshot/verdict handlers + topbar render"
```

---

## Task 6: WS worker — URL builder + pure frame handler + connect loop

**Files:**
- Create: `redrobin/tui/textual_ui/ws_worker.py`
- Test: `tests/unit/tui_textual/test_ws_worker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/tui_textual/test_ws_worker.py`:
```python
from redrobin.tui.textual_ui import ws_worker
from redrobin.tui.textual_ui.frames import Registry


class _Target:
    pass


def test_build_ws_url_appends_token():
    url = ws_worker.build_ws_url("ws://127.0.0.1:8765/v1/tui", "TOK")
    assert url == "ws://127.0.0.1:8765/v1/tui?token=TOK"


def test_build_ws_url_no_token():
    url = ws_worker.build_ws_url("ws://h/v1/tui", None)
    assert url == "ws://h/v1/tui"


def test_handle_raw_dispatches_valid_frame():
    reg = Registry()
    seen = {}
    reg.register("opsec.verdict")(lambda t, p: seen.update(p))
    target = _Target()

    ws_worker.handle_raw(target=target, raw='{"type":"opsec.verdict","payload":{"verdict":"BLOCK"}}', registry=reg)

    assert seen == {"verdict": "BLOCK"}


def test_handle_raw_ignores_bad_json():
    reg = Registry()
    # Must not raise.
    assert ws_worker.handle_raw(target=None, raw="not json", registry=reg) is False


def test_handle_raw_ignores_unknown_type():
    reg = Registry()
    assert ws_worker.handle_raw(target=None, raw='{"type":"weird"}', registry=reg) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/tui_textual/test_ws_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: redrobin.tui.textual_ui.ws_worker`.

- [ ] **Step 3: Write the worker**

Create `redrobin/tui/textual_ui/ws_worker.py`:
```python
"""WS worker for the Textual client: connect to /v1/tui and feed each frame
through the registry. Mirrors the legacy ws_client's connect/auth, but updates
reactive state instead of calling invalidate()."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import connect

from redrobin.tui.textual_ui.frames import Registry

logger = logging.getLogger(__name__)


def build_ws_url(base: str, token: str | None) -> str:
    """Append ``?token=`` — the daemon authenticates /v1/tui via query param."""
    if not token:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({'token': token})}"


def handle_raw(*, target: Any, raw: str, registry: Registry) -> bool:
    """Decode one WS text frame and dispatch it. Never raises (a bad frame
    must not kill the worker loop). Returns True if a handler ran."""
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("ws_worker: dropped non-JSON frame")
        return False
    if not isinstance(frame, dict):
        return False
    return registry.dispatch(target=target, frame=frame)


async def ws_loop(*, target: Any, url: str, token: str | None,
                  registry: Registry, stop_event: asyncio.Event) -> None:
    """Auto-reconnecting consumer. ``target`` is the RedRobinApp; ``registry``
    routes frames to handlers that set its reactive state."""
    full = build_ws_url(url, token)
    async for ws in connect(full, ping_interval=20, ping_timeout=20):
        try:
            async for raw in ws:
                if stop_event.is_set():
                    return
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                handle_raw(target=target, raw=raw, registry=registry)
        except Exception as exc:  # noqa: BLE001 — reconnect on any socket error
            logger.warning("ws_worker reconnecting after error: %s", exc)
            if stop_event.is_set():
                return
            continue
        if stop_event.is_set():
            return


__all__ = ["build_ws_url", "handle_raw", "ws_loop"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/tui_textual/test_ws_worker.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add redrobin/tui/textual_ui/ws_worker.py tests/unit/tui_textual/test_ws_worker.py
git commit -m "feat(tui): WS worker (url builder + pure handle_raw + reconnect loop)"
```

---

## Task 7: Wire the worker on mount + `python -m` entrypoint

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`

- [ ] **Step 1: Add worker startup + main() to the app**

Edit `redrobin/tui/textual_ui/app.py`.

Add to the imports block:
```python
import asyncio
import os
```

Add this method **inside** `class RedRobinApp` (after `watch_snapshot`):
```python
    def on_mount(self) -> None:
        """Start the WS worker unless disabled (tests pass start_worker=False)."""
        if not self._start_worker:
            return
        from redrobin.tui.textual_ui.ws_worker import ws_loop

        self._stop_event = asyncio.Event()
        url = self._ws_url or os.environ.get(
            "REDROBIN_DAEMON_WS", "ws://127.0.0.1:8765/v1/tui"
        )
        self.run_worker(
            ws_loop(target=self, url=url, token=self._token,
                    registry=REGISTRY, stop_event=self._stop_event),
            name="ws", exclusive=True,
        )

    def on_unmount(self) -> None:
        stop = getattr(self, "_stop_event", None)
        if stop is not None:
            stop.set()
```

Add at the **end** of the file:
```python
def main() -> None:
    """Dev entry: ``python -m redrobin.tui.textual_ui.app``. The CLI entrypoint
    (`redrobin tui`) is swapped to this in Phase 6."""
    from redrobin.application.transport_auth import get_or_create_token

    try:
        token = get_or_create_token()
    except Exception:  # noqa: BLE001 — show the app even without a token file
        token = None
    RedRobinApp(token=token).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the app still builds headless (no regression)**

Run: `uv run pytest tests/unit/tui_textual/test_app_layout.py tests/unit/tui_textual/test_app_reactive.py -v`
Expected: all pass (the worker is off in these tests via `start_worker=False`).

- [ ] **Step 3: Commit**

```bash
git add redrobin/tui/textual_ui/app.py
git commit -m "feat(tui): start WS worker on mount + python -m entrypoint"
```

---

## Task 8: Integration — daemon frame types match the client registry

**Files:**
- Test: `tests/integration/test_textual_ws_contract.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_textual_ws_contract.py`:
```python
"""The daemon emits exactly the frame types the Textual client registry knows.
Reuses the daemon TestClient pattern from tests/e2e/test_retroactive_journey.py."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.tui.textual_ui.app import REGISTRY  # registers wm_snapshot + opsec.verdict


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token
    return get_or_create_token()


def _recv_type(ws, wanted: str):
    while True:
        frame = ws.receive_json()
        if frame.get("type") == wanted:
            return frame


def test_connect_snapshot_type_is_handled(daemon):
    app, client = daemon
    token = _token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"
        assert REGISTRY.has_handler(first["type"]) is True


def test_published_verdict_type_is_handled(daemon):
    app, client = daemon
    token = _token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # drain connect snapshot
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "opsec.verdict",
             "payload": {"correlation_id": "c1", "verdict": "BLOCK",
                         "command": "mimikatz", "source": "layer_a"}},
        )
        frame = _recv_type(ws, "opsec.verdict")
        assert REGISTRY.has_handler(frame["type"]) is True
        # And handle_raw routes it without raising.
        from redrobin.tui.textual_ui import ws_worker

        class _T:  # minimal target; the snapshot/verdict handlers set attrs
            snapshot = {}
            events = []
        # opsec.verdict handler does app.events = app.events + [payload]
        t = _T()
        assert ws_worker.handle_raw(target=t, raw=json.dumps(frame), registry=REGISTRY) is True
        assert t.events[-1]["verdict"] == "BLOCK"
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `uv run pytest tests/integration/test_textual_ws_contract.py -v`
Expected: PASS (the registry already handles both types from Task 5). If `test_published_verdict_type_is_handled` errors on `_T.events` mutation, confirm the `opsec.verdict` handler reassigns (`app.events = app.events + [payload]`) — a plain class attr list supports that.

*(This test is written last but should pass immediately — it is the integration **acceptance** that the daemon's emitted frame types and the client registry agree. If it fails, a frame-type contract drifted.)*

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_textual_ws_contract.py
git commit -m "test(tui): daemon frame types match the Textual client registry"
```

---

## Task 9: Full-suite gate + manual launch note

**Files:**
- Modify: `docs/superpowers/TODO.md` (mark Phase 1 in progress / done)

- [ ] **Step 1: Run the full suite (catch cross-cutting regressions)**

Run: `uv run pytest tests/`
Expected: all green (the new tests pass; nothing in the untouched daemon/legacy-TUI regressed — the new package imports nothing from the legacy client).

- [ ] **Step 2: Manual launch sanity (operator, optional pre-smoke)**

With a daemon running locally:
```bash
REDROBIN_DAEMON_WS=ws://127.0.0.1:7777/v1/tui uv run python -m redrobin.tui.textual_ui.app
```
Expected: the Textual app opens; the topbar shows `Red Robin · <engagement> · ●` once the connect snapshot arrives; `q` quits. (Panels are placeholders in Phase 1.)

- [ ] **Step 3: Note progress in TODO.md**

Under the "TUI hardening + UX" section, add a line: `Phase 1 (Textual foundation) ✅ — app shell + theme + WS worker + reactive store + frame registry + reserved advisor-dock seam; #155 dissolved (reactive render).`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/TODO.md
git commit -m "docs: mark TUI Textual Phase 1 (foundation) complete"
```

---

## Self-review

**Spec coverage (Phase 1 portion of §2/§3/§4):**
- App shell + layout (S1 skeleton) → Tasks 4, 5. ✅
- Theme tokens (S2 foundation) → Task 3. ✅
- Data-flow / WS worker / reactive store (S5) → Tasks 5, 6, 7. ✅
- #155 dissolves (reactive, no manual repaint) → Task 5 (unit proof) + Task 9 (manual) + the acceptance note. ✅
- Extensibility seams: frame registry (#1) → Task 2; reserved `#advisor-dock` (#2) → Tasks 3/4; reactive store (#3) → Task 5; daemon already extensible (#5) → Task 8 contract. Provider-based palette (#4) is **Phase 5** (command palette) — not in Phase 1; noted below. ✅ (partial by design)
- Panels detail (DataTable/RichLog), color application, Inspector, palette, #156 → **Phases 2–6** (out of Phase 1). ✅
- Daemon untouched → no daemon files modified in any task. ✅

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands have expected output. ✅

**Type/name consistency:** `Registry.register/dispatch/has_handler`, `REGISTRY` module singleton, `handle_raw(target, raw, registry)`, `build_ws_url(base, token)`, `ws_loop(target, url, token, registry, stop_event)`, `RedRobinApp(start_worker, ws_url, token)`, reactives `snapshot`/`events` — used consistently across Tasks 2–8. ✅

**Fix applied inline:** Task 5 registers handlers referencing `RedRobinApp` via the `target` arg (not a direct class reference at registration), so handler registration does not depend on class definition order.

---

## Subsequent plans (Phases 2–6 — written after this ships)

Each is its own `/writing-plans` doc, branched on the same `tui/textual-redesign` line, each green before the next:

- **Phase 2 — Panels:** Sessions `DataTable`, Verdicts/Events `RichLog` (color-coded, Layer B in-place correction by `correlation_id`, no raw JSON / #148), Working Memory pane — all bound to the reactive store. + Pilot/snapshot tests.
- **Phase 3 — Color + chrome:** apply `$verdict-*` tokens, TopBar status (connection ●, verdict counts), Footer polish, 16-color fallback, responsive min-80×24.
- **Phase 4 — Inspector `ModalScreen`:** section nav + counts, filter/search, group-by kind|beacon, scroll, copy-row.
- **Phase 5 — Navigation:** Command Palette providers (seam #4), `BINDINGS`, `?` help/keymap, focus model.
- **Phase 6 — `#156` + entrypoint swap:** seed `WorkingMemory` from `engagement.yaml` at load (daemon-side); swap `redrobin/cli_helpers/tui_cmd.py:run_tui` → the Textual app; retire the legacy `redrobin/tui/*.py` prompt_toolkit client; full snapshot-test suite.
