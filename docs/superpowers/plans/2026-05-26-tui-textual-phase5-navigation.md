# TUI Textual Redesign — Phase 5 (Navigation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the navigation layer — `Tab`/`Shift+Tab` focus-cycling across the three panes, a `?` keymap help overlay, and a provider-based Command Palette (`Ctrl+P`) registering Red-Robin commands — so the dashboard is discoverable and quick to drive.

**Architecture:** Three small, additive pieces. (1) Focus-cycling needs only making `MemoryPanel` focusable — Textual's default `Tab`/`Shift+Tab` then cycles exactly the three pane widgets (verified), and Phase 3's `:focus-within` accent already marks the active pane. (2) A `HelpScreen(ModalScreen)` (new `help_screen.py`) lists the keymap, opened by `?`. (3) A `RedRobinCommands(Provider)` (new `commands.py`) contributes commands (Inspector, focus-a-pane, help) to the built-in palette via `App.COMMANDS` — this is extensibility seam #4 (a feature registers its commands from its own module; no central keymap edit). Built alongside the legacy client; daemon/bus/.cna untouched.

**Tech Stack:** Python 3.11, Textual 8.2.7 (`command.Provider`/`Hit`/`DiscoveryHit`/`Provider.matcher`, `App.COMMANDS`, `ModalScreen`/`dismiss`, `BINDINGS`, default `Tab`→`focus_next`, `can_focus`, `App.action_command_palette`), pytest (strict asyncio) + `uv run --no-sync`.

---

## Constraints (hard — every task)

- **Branch:** `tui/phase-5-navigation` off `dev-juan` (`3aa416d`). Build alongside the legacy prompt_toolkit client; daemon/bus/reducer/pipeline/.cna untouched. Only `redrobin/tui/textual_ui/*` + its tests change.
- **Tests:** `uv run --no-sync pytest ...` (operator daemon may hold the venv exe → `--no-sync`). Strict asyncio → every async test keeps `@pytest.mark.asyncio`. Query a pushed modal via **`app.screen.query_one(...)`** (not `app.query_one`). **Module-level test imports go at the TOP of the file** (ruff `E402`).
- **Lint/format gates (CI runs on `.`):** `ruff check .` + `ruff format --check .` must pass. **No `# noqa: BLE001`** (unused → RUF100). `redrobin/tui/` is not mypy-gated.
- **Commits:** atomic, conventional subject, **no AI/Claude co-author trailer**.
- **Verified against installed Textual 8.2.7 (while writing this plan):**
  - **Focus:** `DataTable`/`RichLog` are `can_focus=True`; `Static` is `can_focus=False`; `Footer.can_focus=False` (NOT in the tab cycle). With `MemoryPanel` made focusable, default `Tab` cycles exactly `sessions-table → events-log → memory-panel` (wrapping); `Shift+Tab` reverses. Initial focus = first focusable (`sessions-table`).
  - **Command palette:** `App.COMMANDS` is a `set` (default 1 member); extend via `COMMANDS = App.COMMANDS | {RedRobinCommands}`. `command.Provider` has `async def search(self, query)` + `async def discover(self)` (both return `Hits = AsyncIterator[DiscoveryHit | Hit]`) and a `self.matcher(query)` helper (`.match(text)->score`, `.highlight(text)->renderable`). `Hit(score, match_display, command, text=None, help=None)`; `DiscoveryHit(display, command, text=None, help=None)`. The palette is enabled by default and opened by `Ctrl+P` / `action_command_palette`.
  - **Help:** a `ModalScreen` bound to `?` works — `("question_mark", "help", ...)` fires when the user presses `?` (confirmed in a Pilot). `Esc`/`?` close it via `dismiss()`.
- **`i` Inspector binding (Phase 4) + the Phase-1 frame registry:** independent of the palette. The palette commands *call* `app.action_inspect()` (reuse), and the registry routes WS frames (unrelated to key/command input). No interaction issues.

---

## File Structure

- **Modify** `redrobin/tui/textual_ui/panels.py` — `MemoryPanel` gains `can_focus = True` (so Tab can land on it / its pane shows the accent).
- **Create** `redrobin/tui/textual_ui/help_screen.py` — `HelpScreen(ModalScreen)` listing the keymap.
- **Create** `redrobin/tui/textual_ui/commands.py` — `command_specs(app)` + `RedRobinCommands(Provider)`.
- **Modify** `redrobin/tui/textual_ui/app.py` — add `("question_mark", "help", "Help")` to `BINDINGS` + `action_help` (pushes `HelpScreen`); set `COMMANDS = App.COMMANDS | {RedRobinCommands}`.
- **Modify** `redrobin/tui/textual_ui/theme.tcss` — style the help modal.
- **Create** `tests/unit/tui_textual/test_navigation.py` — focus-cycle, help overlay, command provider tests.

---

## Task 1: Focus-cycling across the three panes

**Files:**
- Modify: `redrobin/tui/textual_ui/panels.py`
- Test: `tests/unit/tui_textual/test_navigation.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/unit/tui_textual/test_navigation.py`:

```python
import pytest

from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_tab_cycles_the_three_panes():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Initial focus is the first focusable pane widget.
        assert app.focused is not None and app.focused.id == "sessions-table"
        order = []
        for _ in range(3):
            await pilot.press("tab")
            await pilot.pause()
            order.append(app.focused.id)
        assert order == ["events-log", "memory-panel", "sessions-table"]


@pytest.mark.asyncio
async def test_shift_tab_reverses():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == "memory-panel"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -v`
Expected: FAIL — `memory-panel` is a `Static` (`can_focus=False`), so it is skipped; the tab order is `events-log → sessions-table → events-log` (only 2 panes) and `assert order == [...]` fails.

- [ ] **Step 3: Make `MemoryPanel` focusable** — in `redrobin/tui/textual_ui/panels.py`, add the class attribute to `MemoryPanel` (right under the class docstring, before `update_from_snapshot`):

```python
class MemoryPanel(Static):
    """Working-memory summary: phase, scope (in/out), and counts."""

    can_focus = True

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.update(format_memory(snapshot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -v`
Expected: PASS (2 tests). Also run the existing panel + app tests to confirm no regression: `uv run --no-sync pytest tests/unit/tui_textual/test_panels.py tests/unit/tui_textual/test_app_phase2.py -q`.

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_navigation.py && uv run --no-sync ruff format redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_navigation.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/panels.py tests/unit/tui_textual/test_navigation.py
git commit -m "feat(tui): Tab/Shift+Tab focus-cycle across the three panes"
```

---

## Task 2: `?` keymap help overlay

**Files:**
- Create: `redrobin/tui/textual_ui/help_screen.py`
- Modify: `redrobin/tui/textual_ui/app.py`
- Modify: `redrobin/tui/textual_ui/theme.tcss`
- Test: `tests/unit/tui_textual/test_navigation.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/tui_textual/test_navigation.py`:

```python
@pytest.mark.asyncio
async def test_question_mark_opens_help():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        from textual.widgets import Static

        body = app.screen.query_one("#help-body", Static)
        text = str(body.content)
        assert "Inspector" in text
        assert "Command palette" in text


@pytest.mark.asyncio
async def test_help_escape_closes():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "HelpScreen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -k "help" -v`
Expected: FAIL — no `?` binding / `HelpScreen` doesn't exist.

- [ ] **Step 3: Create `HelpScreen`** — create `redrobin/tui/textual_ui/help_screen.py`:

```python
"""HelpScreen — a `?` keymap overlay for the Textual TUI (Phase 5).

A read-only ModalScreen listing the key bindings, the middle layer of
discoverability (Footer → ? full keymap → Ctrl+P command search)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

# (key, description) pairs. Dashboard keys first, then the Inspector's.
_KEYMAP: list[tuple[str, str]] = [
    ("i", "Open the Inspector (full engagement state)"),
    ("Tab / Shift+Tab", "Cycle focus across the panels"),
    ("Ctrl+P", "Command palette (fuzzy command search)"),
    ("?", "This keymap"),
    ("q", "Quit"),
    ("", ""),
    ("— in the Inspector —", ""),
    ("↑ / ↓", "Navigate the section"),
    ("(type)", "Filter the active section"),
    ("g", "Group findings (flat → kind → beacon)"),
    ("y", "Copy the highlighted row"),
    ("Esc", "Close"),
]


class HelpScreen(ModalScreen):
    """Keymap overlay. `?` or `Esc` closes it."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("question_mark", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static("Keymap", id="help-title")
            yield Static(
                "\n".join(f"{key:18}{desc}" if key else "" for key, desc in _KEYMAP),
                id="help-body",
            )
            yield Static("Esc or ? to close", id="help-hint")

    def action_close(self) -> None:
        self.dismiss()
```

- [ ] **Step 4: Wire the `?` binding into `RedRobinApp` (`app.py`)**

(a) Add the import (near the other `from redrobin.tui.textual_ui...` imports):

```python
from redrobin.tui.textual_ui.help_screen import HelpScreen
```

(b) Add `("question_mark", "help", "Help")` to `BINDINGS`:

```python
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("i", "inspect", "Inspector"),
        ("question_mark", "help", "Help"),
    ]
```

(c) Add the action method (place it next to `action_inspect`):

```python
    def action_help(self) -> None:
        """Open the keymap help overlay."""
        self.push_screen(HelpScreen())
```

- [ ] **Step 5: Style the help modal — APPEND to `redrobin/tui/textual_ui/theme.tcss`:**

```css
/* Help modal (Phase 5): a small centered keymap overlay. */
HelpScreen {
    align: center middle;
}
#help {
    width: 60;
    height: auto;
    max-height: 90%;
    background: $panel;
    border: round $accent;
    padding: 1 2;
}
#help-title {
    text-style: bold;
    color: $text;
}
#help-hint {
    color: $text-muted;
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -v`
Expected: PASS (the 2 focus tests + the 2 help tests). The help tests query the modal via `app.screen.query_one(...)`.

- [ ] **Step 7: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/help_screen.py redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_navigation.py && uv run --no-sync ruff format redrobin/tui/textual_ui/help_screen.py redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_navigation.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add redrobin/tui/textual_ui/help_screen.py redrobin/tui/textual_ui/app.py redrobin/tui/textual_ui/theme.tcss tests/unit/tui_textual/test_navigation.py
git commit -m "feat(tui): '?' keymap help overlay"
```

---

## Task 3: Command palette provider

**Files:**
- Create: `redrobin/tui/textual_ui/commands.py`
- Modify: `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_navigation.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/tui_textual/test_navigation.py`:

```python
@pytest.mark.asyncio
async def test_command_specs_open_inspector():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        from redrobin.tui.textual_ui.commands import command_specs

        specs = command_specs(app)
        names = [name for name, _, _ in specs]
        assert any("Inspector" in n for n in names)
        assert any("Sessions" in n for n in names)
        assert any("Working Memory" in n for n in names)
        runnable = next(cmd for name, cmd, _ in specs if "Inspector" in name)
        runnable()
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"


@pytest.mark.asyncio
async def test_command_specs_focus_a_pane():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        from redrobin.tui.textual_ui.commands import command_specs

        runnable = next(cmd for name, cmd, _ in command_specs(app) if "Verdicts" in name)
        runnable()
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "events-log"


def test_app_registers_red_robin_command_provider():
    from redrobin.tui.textual_ui.app import RedRobinApp
    from redrobin.tui.textual_ui.commands import RedRobinCommands

    assert RedRobinCommands in RedRobinApp.COMMANDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -k "command_specs or registers" -v`
Expected: FAIL — `commands` module / `RedRobinCommands` don't exist; `RedRobinApp.COMMANDS` is the default.

- [ ] **Step 3: Create the provider** — create `redrobin/tui/textual_ui/commands.py`:

```python
"""Command-palette provider for the Textual TUI (Phase 5).

Extensibility seam #4: a feature contributes its commands from its own module.
``command_specs`` is the single source of (name, runnable, help) tuples;
``RedRobinCommands`` is the thin Textual ``Provider`` that surfaces them in the
built-in palette (Ctrl+P). Registered via ``RedRobinApp.COMMANDS``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from textual.app import App


def command_specs(app: "App") -> list[tuple[str, Callable[[], None], str]]:
    """The Red-Robin palette commands as (display, runnable, help) tuples.
    Reuses the app's existing actions/widgets — adding a command is a new tuple here."""

    def _focus(widget_id: str) -> Callable[[], None]:
        return lambda: app.query_one(widget_id).focus()

    return [
        ("Inspector — full engagement state", app.action_inspect, "Open the Inspector modal"),
        ("Focus Sessions", _focus("#sessions-table"), "Focus the Sessions pane"),
        ("Focus Verdicts / Events", _focus("#events-log"), "Focus the Verdicts/Events pane"),
        ("Focus Working Memory", _focus("#memory-panel"), "Focus the Working Memory pane"),
        ("Keymap help", app.action_help, "Show the keymap overlay"),
    ]


class RedRobinCommands(Provider):
    """Surfaces ``command_specs`` in the command palette (fuzzy search + discovery)."""

    async def discover(self) -> Hits:
        for display, runnable, help_text in command_specs(self.app):
            yield DiscoveryHit(display, runnable, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for display, runnable, help_text in command_specs(self.app):
            score = matcher.match(display)
            if score > 0:
                yield Hit(score, matcher.highlight(display), runnable, help=help_text)
```

- [ ] **Step 4: Register the provider in `RedRobinApp` (`app.py`)**

(a) Add the import:

```python
from redrobin.tui.textual_ui.commands import RedRobinCommands
```

(b) Add the `COMMANDS` class var to `RedRobinApp` (right after the `BINDINGS` line):

```python
    COMMANDS = App.COMMANDS | {RedRobinCommands}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_navigation.py -v`
Expected: PASS (all navigation tests — focus, help, command specs, registration).

- [ ] **Step 6: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/commands.py redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_navigation.py && uv run --no-sync ruff format redrobin/tui/textual_ui/commands.py redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_navigation.py`
Expected: clean. (`App` is imported in `app.py` already; `RedRobinCommands` is used in `COMMANDS`.)

- [ ] **Step 7: Commit**

```bash
git add redrobin/tui/textual_ui/commands.py redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_navigation.py
git commit -m "feat(tui): command palette provider (Inspector + focus-pane + help)"
```

---

## Task 4: Full-suite gate + docs

**Files:**
- Modify: `docs/superpowers/TODO.md`

- [ ] **Step 1: Run the exact CI gates locally**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync lint-imports --config importlinter.cfg
uv run --no-sync pytest tests/ -q
```
Expected: ruff clean; format reports all files already formatted; import-linter `4 kept, 0 broken`; pytest all green (Phase 5 adds ~9 tests on top of 539). If `ruff format --check .` flags anything, run `ruff format .` and re-run.

- [ ] **Step 2: Mark Phase 5 done in `docs/superpowers/TODO.md`** — change the Phase 5 line from `- [ ]` to `- [x]` (fill `N`/`M`):

```markdown
- [x] **Phase 5 — navigation** ✅ — `Tab`/`Shift+Tab` focus-cycle the 3 panes (active = accent border); `?` keymap help overlay; Command Palette (`Ctrl+P`) via a provider seam (Inspector + focus-pane + help commands). Plan: [plans/2026-05-26-tui-textual-phase5-navigation.md](./plans/2026-05-26-tui-textual-phase5-navigation.md). *(branch `tui/phase-5-navigation`; N new tests; M suite green.)*
```

Also update the "Now / Next" line to point at Phase 6.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/TODO.md
git commit -m "docs: mark TUI Phase 5 (navigation) complete"
```

---

## Self-Review (completed while writing)

**1. Spec coverage (§3.4):**
- Command Palette (`Ctrl+P`, provider-based, fuzzy) → `RedRobinCommands(Provider)` + `App.COMMANDS` (Task 3); built-in palette opens it; seam #4 satisfied (commands live in `commands.py`, added by appending a tuple). ✓
- Focus model `⇥/⇧⇥` cycle panels (active = accent) → `MemoryPanel.can_focus` + default Tab cycle + Phase-3 `:focus-within` (Task 1). ✓ *(`↑↓ within / ↵ act` are the focused widget's own behavior — DataTable/RichLog/OptionList handle them natively; `/` filter applies inside the Inspector, already Phase 4.)*
- Discoverability in layers: Footer (frequent keys, already present + now shows `?`) → `?` full keymap (Task 2) → `Ctrl+P` command search (Task 3). ✓

**2. Placeholder scan:** none — every step has concrete code/commands + expected output.

**3. Type/name consistency:** `command_specs`/`RedRobinCommands`/`HelpScreen`/`action_help`/`COMMANDS`/`can_focus`, widget ids (`#sessions-table`/`#events-log`/`#memory-panel`/`#help`/`#help-body`/`#help-title`/`#help-hint`), binding `question_mark` — consistent across tasks. `command_specs` reuses `app.action_inspect` (Phase 4) + `app.action_help` (Task 2, defined before Task 3 uses it). Tests query modals via `app.screen.query_one`; imports at file top (no E402).

---

## Execution Notes

- **Operator smoke (after Task 4, before PR):** live daemon → `python -m redrobin.tui.textual_ui.app`. Verify: `Tab`/`Shift+Tab` moves the accent border across Sessions → Verdicts → Working Memory (and back); `?` opens the keymap overlay (`Esc`/`?` closes); `Ctrl+P` opens the command palette and typing `insp` surfaces "Inspector — full engagement state" (Enter opens it), `focus verd` surfaces "Focus Verdicts / Events" (Enter moves the accent). The system commands (change theme, quit) still appear in the palette.
- **PR:** `tui/phase-5-navigation` → `dev-juan` (CI runs on the merge push). Then Phase 6 (#156 WM-seed + entrypoint swap → retire the legacy prompt_toolkit client) — the last phase.
