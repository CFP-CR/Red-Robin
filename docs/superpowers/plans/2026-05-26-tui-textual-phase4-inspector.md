# TUI Textual Redesign — Phase 4 (Inspector ModalScreen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-engagement-state Inspector modal (`i` opens, `Esc` closes) with a left section-nav (counts), a filter/search box, group-by for findings, native scroll, and copy-row — taming the 148-findings dump that overwhelmed the legacy single-string inspector.

**Architecture:** A new `redrobin/tui/textual_ui/inspector.py` holds (1) **pure** section helpers — `SECTIONS`, `section_counts(snapshot)`, `render_section(snapshot, key, filter_text, group_by)` — that turn the app's snapshot **dict** into per-section text lines ( porting the legacy `redrobin/tui/inspector.py` formatting, but dict-based, not `WorkingMemory`-based), and (2) `InspectorScreen(ModalScreen)` — a nav `OptionList` + a filter `Input` + a content `OptionList`, driven by those helpers. `RedRobinApp` gains an `i` binding that pushes the screen with the current `snapshot`. Built alongside the legacy `inspector.py`; the daemon/bus/.cna are untouched.

**Tech Stack:** Python 3.11, Textual 8.2.7 (`ModalScreen`/`push_screen`/`dismiss`/`OptionList`/`Option`/`Input`/`Horizontal`/`Vertical`/`Static`/`App.copy_to_clipboard`/`App.notify`/`run_test` Pilot), `.tcss`, pytest (strict asyncio) + `uv run --no-sync`.

---

## Constraints (hard — every task)

- **Branch:** `tui/phase-4-inspector` off `dev-juan` (`347a8a0`). Build **alongside** the legacy prompt_toolkit client + its `redrobin/tui/inspector.py`. The daemon, bus, reducer, pipeline, and `.cna` are **untouched**. Only `redrobin/tui/textual_ui/*` + its tests change.
- **Tests:** `uv run --no-sync pytest ...` (operator daemon may hold the venv exe → `--no-sync`). Strict asyncio → every async test keeps `@pytest.mark.asyncio`. **A Pilot test must query a pushed modal via `app.screen.query_one(...)`, NOT `app.query_one(...)`** (the latter scopes to the base screen and raises `NoMatches`).
- **Lint/format gates (CI runs on `.`):** `uv run --no-sync ruff check .` and `ruff format --check .` must pass. **Do NOT add `# noqa: BLE001`** (unused → RUF100). `redrobin/tui/` is not mypy-gated, but keep types clean.
- **Commits:** atomic, conventional-commit subject, **no AI/Claude co-author trailer**.
- **Verified against installed Textual 8.2.7 (while writing this plan):** `ModalScreen` (bases `ModalScreen→Screen→Generic`); `App.push_screen(screen)` makes it the active `app.screen`; `Screen.dismiss(result=None)`; `App.copy_to_clipboard(text)` and `App.notify(msg)` exist; `Input(value=, placeholder=, id=)` + `Input.Changed` message (setting `.value` posts it); `OptionList(*options, id=)` + `.add_option`/`.add_options`/`.clear_options`/`.option_count`/`.highlighted` (settable int|None) / `.get_option_at_index(i)`; `Option(prompt, id=)` with `.prompt`/`.id`; `OptionList.OptionHighlighted` event (handler `on_option_list_option_highlighted`, attrs `.option_list`, `.option`, `.option_id`, `.option_index`). A **plain `i`** binding fires `action_inspect` and `push_screen` activates the modal; `escape` bound to `action_close→dismiss()` pops it (empirically confirmed in a Pilot).
- **Open key = `i` (NOT `ctrl+i`):** terminals send the same byte (`HT`/`0x09`) for Ctrl+I and Tab, so a `ctrl+i` binding is dead in the operator's Windows Terminal and would collide with Phase 5's Tab focus-cycling. The spec's `^I` notation is superseded by `i`.

---

## Snapshot shape (from `working_memory_to_snapshot` = `asdict(WorkingMemory)`)

`render_section` reads these dict keys (each list item is a plain dict — `asdict` recurses):
- `engagement_id: str`, `client: str`, `current_phase: str`
- `scope_in_scope: list[str]`, `scope_out_of_scope: list[str]`, `rules_of_engagement: list[str]`, `defenses_observed: list[str]`
- `kill_chain_status: dict[str,str]`
- `objectives: list[{id, goal, status, progress_pct, priority, milestones}]`
- `hosts: list[{name, os, integrity_level, edr_detected, access_level}]`
- `accounts: list[{name, kind, source_host, captured_at}]`
- `tools_used: list[{name, used_on, used_at, detection_observed}]`
- `credentials: list[{domain, username, ntlm_hash, source_beacon_id}]`
- `findings: list[{kind, summary, source_beacon_id}]`

(All accessed with `.get(...)` + `or` fallbacks — the snapshot may omit keys early in an engagement.)

---

## File Structure

- **Create** `redrobin/tui/textual_ui/inspector.py` — pure helpers (`SECTIONS`, `section_counts`, `render_section`) + `InspectorScreen(ModalScreen)`.
- **Modify** `redrobin/tui/textual_ui/app.py` — add the `i` → `action_inspect` binding that pushes `InspectorScreen(self.snapshot)`.
- **Modify** `redrobin/tui/textual_ui/theme.tcss` — style the modal (centered dialog, nav width, docked title/hint).
- **Create** `tests/unit/tui_textual/test_inspector.py` — pure-helper tests + Pilot tests (open, nav, filter, group, copy, close).

---

## Task 1: Section model + counts + flat render (inspector.py)

**Files:**
- Create: `redrobin/tui/textual_ui/inspector.py`
- Test: `tests/unit/tui_textual/test_inspector.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/tui_textual/test_inspector.py`:

```python
from redrobin.tui.textual_ui import inspector

SNAP = {
    "engagement_id": "e2e-verify",
    "client": "Acme",
    "current_phase": "recon",
    "scope_in_scope": ["EC2AMAZ-IJVFMFN", "10.100.0.0/24"],
    "scope_out_of_scope": ["10.99.0.0/16"],
    "rules_of_engagement": ["no DoS"],
    "defenses_observed": ["CrowdStrike"],
    "kill_chain_status": {"recon": "done", "access": "in_progress"},
    "objectives": [{"id": "o1", "goal": "Compromise DC", "status": "active", "progress_pct": 60, "priority": "high", "milestones": []}],
    "hosts": [{"name": "EC2AMAZ-IJVFMFN", "os": "Win2022", "integrity_level": "High", "edr_detected": "CrowdStrike", "access_level": "SYSTEM"}],
    "accounts": [{"name": "CORP\\\\alice", "kind": "ntlm", "source_host": "EC2AMAZ", "captured_at": "t0"}],
    "tools_used": [{"name": "mimikatz", "used_on": "EC2AMAZ", "used_at": "t1", "detection_observed": "blocked"}],
    "credentials": [{"domain": "CORP", "username": "alice", "ntlm_hash": "aad3b4", "source_beacon_id": "b1"}],
    "findings": [
        {"kind": "kerberos_ticket_observed", "summary": "TGT for alice", "source_beacon_id": "b1"},
        {"kind": "opsec_block", "summary": "mimikatz blocked", "source_beacon_id": "b2"},
    ],
}


def test_sections_match_spec_order():
    keys = [k for k, _ in inspector.SECTIONS]
    assert keys[0] == "engagement"
    assert "findings" in keys
    assert keys[-1] == "findings"
    # all titles are upper-case display labels
    assert all(title.upper() == title for _, title in inspector.SECTIONS)


def test_section_counts():
    counts = inspector.section_counts(SNAP)
    assert counts["findings"] == 2
    assert counts["credentials"] == 1
    assert counts["hosts"] == 1
    assert counts["objectives"] == 1
    assert counts["engagement"] is None  # singular section, no count


def test_render_findings_lines():
    lines = inspector.render_section(SNAP, "findings")
    assert any("kerberos_ticket_observed" in line and "TGT for alice" in line for line in lines)
    assert any("b2" in line for line in lines)
    assert len(lines) == 2


def test_render_engagement_lines():
    lines = inspector.render_section(SNAP, "engagement")
    text = " ".join(lines)
    assert "e2e-verify" in text
    assert "Acme" in text
    assert "recon" in text


def test_render_empty_section_is_graceful():
    lines = inspector.render_section({}, "findings")
    assert lines == ["(none)"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -k "sections or section_counts or render" -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (inspector module/helpers don't exist).

- [ ] **Step 3: Implement the pure helpers** — create `redrobin/tui/textual_ui/inspector.py`:

```python
"""Inspector ModalScreen + pure section helpers for the Textual TUI (Phase 4).

The helpers turn the app's working-memory *snapshot dict* (from
``working_memory_to_snapshot`` = ``asdict(WorkingMemory)``) into per-section text
lines — porting the formatting from the legacy ``redrobin/tui/inspector.py`` but
dict-based and split into navigable sections instead of one overwhelming string.
"""

from __future__ import annotations

from typing import Any

# Ordered (key, display title). Mirrors spec §3.3, plus KILL CHAIN (data is in the
# snapshot and the legacy inspector showed it — kept for parity).
SECTIONS: list[tuple[str, str]] = [
    ("engagement", "ENGAGEMENT"),
    ("scope", "SCOPE"),
    ("roe", "ROE"),
    ("objectives", "OBJECTIVES"),
    ("kill_chain", "KILL CHAIN"),
    ("hosts", "HOSTS"),
    ("accounts", "ACCOUNTS"),
    ("defenses", "DEFENSES"),
    ("tools", "TOOLS"),
    ("credentials", "CREDENTIALS"),
    ("findings", "FINDINGS"),
]


def section_counts(snapshot: dict[str, Any]) -> dict[str, int | None]:
    """Item count per section for the nav labels. ``engagement`` is singular → None."""
    return {
        "engagement": None,
        "scope": len(snapshot.get("scope_in_scope") or [])
        + len(snapshot.get("scope_out_of_scope") or []),
        "roe": len(snapshot.get("rules_of_engagement") or []),
        "objectives": len(snapshot.get("objectives") or []),
        "kill_chain": len(snapshot.get("kill_chain_status") or {}),
        "hosts": len(snapshot.get("hosts") or []),
        "accounts": len(snapshot.get("accounts") or []),
        "defenses": len(snapshot.get("defenses_observed") or []),
        "tools": len(snapshot.get("tools_used") or []),
        "credentials": len(snapshot.get("credentials") or []),
        "findings": len(snapshot.get("findings") or []),
    }


def _engagement_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"ID:     {s.get('engagement_id') or '-'}",
        f"Client: {s.get('client') or '-'}",
        f"Phase:  {s.get('current_phase') or '-'}",
    ]


def _scope_lines(s: dict[str, Any]) -> list[str]:
    out = [f"in:  {x}" for x in (s.get("scope_in_scope") or [])]
    out += [f"out: {x}" for x in (s.get("scope_out_of_scope") or [])]
    return out


def _objective_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{o.get('id', '?')}: {o.get('goal', '')} "
        f"[{o.get('status', '?')}, {o.get('progress_pct', 0)}%, prio={o.get('priority', '?')}]"
        for o in (s.get("objectives") or [])
    ]


def _kill_chain_lines(s: dict[str, Any]) -> list[str]:
    return [f"{phase}: {status}" for phase, status in (s.get("kill_chain_status") or {}).items()]


def _host_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{h.get('name', '?')} [{h.get('os') or '-'}] EDR={h.get('edr_detected') or '-'} "
        f"access={h.get('access_level') or '-'} integrity={h.get('integrity_level') or '-'}"
        for h in (s.get("hosts") or [])
    ]


def _account_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{a.get('name', '?')} [{a.get('kind', '?')}] source={a.get('source_host') or '-'} "
        f"captured={a.get('captured_at') or '-'}"
        for a in (s.get("accounts") or [])
    ]


def _tool_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{t.get('name', '?')} on {t.get('used_on') or '-'} at {t.get('used_at') or '-'} "
        f"— {t.get('detection_observed') or '-'}"
        for t in (s.get("tools_used") or [])
    ]


def _credential_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"{c.get('domain', '?')}\\{c.get('username', '?')} "
        f"hash={c.get('ntlm_hash') or '-'} src={c.get('source_beacon_id') or '-'}"
        for c in (s.get("credentials") or [])
    ]


def _finding_lines(s: dict[str, Any]) -> list[str]:
    return [
        f"[{f.get('kind', '?')}] {f.get('summary', '')} (src={f.get('source_beacon_id') or '-'})"
        for f in (s.get("findings") or [])
    ]


def _str_lines(s: dict[str, Any], key: str) -> list[str]:
    return [f"- {x}" for x in (s.get(key) or [])]


def render_section(
    snapshot: dict[str, Any],
    section: str,
    *,
    filter_text: str = "",
    group_by: str | None = None,
) -> list[str]:
    """Text lines for ``section``. ``filter_text`` keeps case-insensitive substring
    matches; ``group_by`` ('kind'|'beacon') groups FINDINGS under sub-headers."""
    if section == "engagement":
        lines = _engagement_lines(snapshot)
    elif section == "scope":
        lines = _scope_lines(snapshot)
    elif section == "roe":
        lines = _str_lines(snapshot, "rules_of_engagement")
    elif section == "objectives":
        lines = _objective_lines(snapshot)
    elif section == "kill_chain":
        lines = _kill_chain_lines(snapshot)
    elif section == "hosts":
        lines = _host_lines(snapshot)
    elif section == "accounts":
        lines = _account_lines(snapshot)
    elif section == "defenses":
        lines = _str_lines(snapshot, "defenses_observed")
    elif section == "tools":
        lines = _tool_lines(snapshot)
    elif section == "credentials":
        lines = _credential_lines(snapshot)
    elif section == "findings":
        lines = _grouped_findings(snapshot, group_by) if group_by else _finding_lines(snapshot)
    else:
        lines = []

    if filter_text:
        needle = filter_text.lower()
        lines = [ln for ln in lines if needle in ln.lower()]
    return lines or ["(none)"]


def _grouped_findings(snapshot: dict[str, Any], group_by: str) -> list[str]:
    """Findings grouped under '── <group> (<n>) ──' sub-headers."""
    key = "kind" if group_by == "kind" else "source_beacon_id"
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in snapshot.get("findings") or []:
        groups.setdefault(str(f.get(key) or "-"), []).append(f)
    out: list[str] = []
    for group_value, items in groups.items():
        out.append(f"── {group_value} ({len(items)}) ──")
        out += [
            f"  [{f.get('kind', '?')}] {f.get('summary', '')} (src={f.get('source_beacon_id') or '-'})"
            for f in items
        ]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/inspector.py tests/unit/tui_textual/test_inspector.py && uv run --no-sync ruff format redrobin/tui/textual_ui/inspector.py tests/unit/tui_textual/test_inspector.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/inspector.py tests/unit/tui_textual/test_inspector.py
git commit -m "feat(tui): inspector section model + dict-based render helpers"
```

---

## Task 2: Filter + group-by (pure)

**Files:**
- Modify: `redrobin/tui/textual_ui/inspector.py` *(already implemented in Task 1 — this task only adds tests proving the behavior; if any test fails, fix `render_section`/`_grouped_findings`)*
- Test: `tests/unit/tui_textual/test_inspector.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/tui_textual/test_inspector.py`:

```python
def test_filter_narrows_findings_case_insensitive():
    lines = inspector.render_section(SNAP, "findings", filter_text="KERBEROS")
    assert len(lines) == 1
    assert "kerberos_ticket_observed" in lines[0]


def test_filter_no_match_returns_none_placeholder():
    lines = inspector.render_section(SNAP, "findings", filter_text="zzz-no-such")
    assert lines == ["(none)"]


def test_group_by_kind_adds_subheaders():
    lines = inspector.render_section(SNAP, "findings", group_by="kind")
    assert any(line.startswith("── kerberos_ticket_observed (1)") for line in lines)
    assert any(line.startswith("── opsec_block (1)") for line in lines)
    # grouped finding lines are indented under their header
    assert any(line.startswith("  [kerberos_ticket_observed]") for line in lines)


def test_group_by_beacon_groups_by_source():
    lines = inspector.render_section(SNAP, "findings", group_by="beacon")
    assert any(line.startswith("── b1 (1)") for line in lines)
    assert any(line.startswith("── b2 (1)") for line in lines)
```

- [ ] **Step 2: Run tests**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -k "filter or group_by" -v`
Expected: PASS (the Task-1 implementation already covers filter + group-by). If any FAIL, fix `render_section`/`_grouped_findings` in `inspector.py` minimally until green.

- [ ] **Step 3: Lint + format**

Run: `uv run --no-sync ruff check tests/unit/tui_textual/test_inspector.py && uv run --no-sync ruff format tests/unit/tui_textual/test_inspector.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/tui_textual/test_inspector.py redrobin/tui/textual_ui/inspector.py
git commit -m "test(tui): cover inspector filter + group-by"
```

---

## Task 3: InspectorScreen shell — nav + content + section switching + close

**Files:**
- Modify: `redrobin/tui/textual_ui/inspector.py`
- Modify: `redrobin/tui/textual_ui/theme.tcss`
- Test: `tests/unit/tui_textual/test_inspector.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/tui_textual/test_inspector.py`:

```python
import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList


class _Host(App):
    def __init__(self, snap):
        super().__init__()
        self._snap = snap

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        yield Static("host")

    def on_mount(self) -> None:
        from redrobin.tui.textual_ui.inspector import InspectorScreen

        self.push_screen(InspectorScreen(self._snap))


@pytest.mark.asyncio
async def test_inspector_opens_with_nav_and_content():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        nav = app.screen.query_one("#inspector-nav", OptionList)
        assert nav.option_count == len(inspector.SECTIONS)
        content = app.screen.query_one("#inspector-content", OptionList)
        # first section (engagement) is rendered
        assert content.option_count >= 1
        assert any("e2e-verify" in str(content.get_option_at_index(i).prompt)
                   for i in range(content.option_count))


@pytest.mark.asyncio
async def test_inspector_nav_switches_section():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # highlight FINDINGS in the nav
        findings_idx = [k for k, _ in inspector.SECTIONS].index("findings")
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = findings_idx
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert any("kerberos_ticket_observed" in p for p in prompts)


@pytest.mark.asyncio
async def test_inspector_escape_dismisses():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "InspectorScreen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -k "inspector_opens or nav_switches or escape" -v`
Expected: FAIL — `InspectorScreen` doesn't exist yet.

- [ ] **Step 3: Implement `InspectorScreen`** — append to `redrobin/tui/textual_ui/inspector.py`:

Add imports at the top (below `from typing import Any`):

```python
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
```

Append the class:

```python
class InspectorScreen(ModalScreen):
    """Full-engagement-state inspector. ``i`` opens it (from the app), ``Esc`` closes.
    Left nav picks a section; the content list shows that section's lines."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("g", "cycle_group", "Group"),
        ("y", "copy_row", "Copy"),
    ]

    def __init__(self, snapshot: dict[str, Any]) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._section = SECTIONS[0][0]
        self._group_by: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="inspector"):
            yield Static(
                f"Memory Inspector — {self._snapshot.get('engagement_id') or '-'}",
                id="inspector-title",
            )
            with Horizontal(id="inspector-main"):
                yield OptionList(id="inspector-nav")
                with Vertical(id="inspector-body"):
                    yield Input(placeholder="filter…", id="inspector-filter")
                    yield OptionList(id="inspector-content")
            yield Static("Esc close · ↑↓ navigate · g group findings · y copy row", id="inspector-hint")

    def on_mount(self) -> None:
        nav = self.query_one("#inspector-nav", OptionList)
        counts = section_counts(self._snapshot)
        for key, title in SECTIONS:
            count = counts.get(key)
            label = f"{title} ({count})" if count is not None else title
            nav.add_option(Option(label, id=key))
        nav.highlighted = 0
        nav.focus()
        self._render_content()

    def _render_content(self) -> None:
        content = self.query_one("#inspector-content", OptionList)
        content.clear_options()
        filter_text = self.query_one("#inspector-filter", Input).value
        lines = render_section(
            self._snapshot, self._section, filter_text=filter_text, group_by=self._group_by
        )
        content.add_options([Option(line) for line in lines])

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        # Only the nav switches sections; the content list's own highlight is for copy-row.
        if event.option_list.id == "inspector-nav" and event.option_id:
            self._section = event.option_id
            self._render_content()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._render_content()

    def action_cycle_group(self) -> None:
        order: list[str | None] = [None, "kind", "beacon"]
        self._group_by = order[(order.index(self._group_by) + 1) % len(order)]
        self._render_content()

    def action_copy_row(self) -> None:
        content = self.query_one("#inspector-content", OptionList)
        index = content.highlighted
        if index is not None:
            text = str(content.get_option_at_index(index).prompt)
            self.app.copy_to_clipboard(text)
            self.app.notify("Copied row to clipboard")

    def action_close(self) -> None:
        self.dismiss()
```

- [ ] **Step 4: Add modal styles to `theme.tcss`** — append:

```css
/* Inspector modal (Phase 4): a centered dialog over a dimmed screen. */
InspectorScreen {
    align: center middle;
}
#inspector {
    width: 90%;
    height: 90%;
    background: $panel;
    border: round $accent;
}
#inspector-title {
    dock: top;
    height: 1;
    color: $text;
    text-style: bold;
}
#inspector-hint {
    dock: bottom;
    height: 1;
    color: $text-muted;
}
#inspector-nav {
    width: 28;
    border-right: solid $panel-lighten-2;
}
#inspector-body {
    width: 1fr;
}
#inspector-filter {
    height: 3;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -v`
Expected: PASS (all pure + the 3 new Pilot tests). Remember: the tests query `app.screen.query_one(...)`.

- [ ] **Step 6: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/inspector.py tests/unit/tui_textual/test_inspector.py && uv run --no-sync ruff format redrobin/tui/textual_ui/inspector.py tests/unit/tui_textual/test_inspector.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add redrobin/tui/textual_ui/inspector.py redrobin/tui/textual_ui/theme.tcss tests/unit/tui_textual/test_inspector.py
git commit -m "feat(tui): InspectorScreen modal — section nav + content + Esc close"
```

---

## Task 4: Filter input + group-by binding (in the modal)

**Files:**
- Modify: `tests/unit/tui_textual/test_inspector.py` *(behavior already wired in Task 3 — `on_input_changed` + `action_cycle_group`; this task proves them via Pilot; fix the modal if a test fails)*
- Test: `tests/unit/tui_textual/test_inspector.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from textual.widgets import Input


@pytest.mark.asyncio
async def test_inspector_filter_narrows_content():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # switch to FINDINGS
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = [k for k, _ in inspector.SECTIONS].index("findings")
        await pilot.pause()
        # set the filter value (setting Input.value posts Input.Changed)
        screen.query_one("#inspector-filter", Input).value = "kerberos"
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert prompts == [p for p in prompts if "kerberos" in p.lower()]
        assert any("kerberos" in p.lower() for p in prompts)
        assert not any("opsec_block" in p for p in prompts)


@pytest.mark.asyncio
async def test_inspector_group_toggle_groups_findings():
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        nav = screen.query_one("#inspector-nav", OptionList)
        nav.highlighted = [k for k, _ in inspector.SECTIONS].index("findings")
        await pilot.pause()
        # nav (an OptionList) doesn't consume 'g' → it bubbles to the screen binding
        # (verified: a focused OptionList passes letter keys through to BINDINGS).
        await pilot.press("g")  # None -> kind
        await pilot.pause()
        content = screen.query_one("#inspector-content", OptionList)
        prompts = [str(content.get_option_at_index(i).prompt) for i in range(content.option_count)]
        assert any(p.startswith("── ") for p in prompts)  # a group sub-header appeared
```

- [ ] **Step 2: Run tests**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -k "filter_narrows_content or group_toggle" -v`
Expected: PASS (Task 3 wired `on_input_changed` + `action_cycle_group`). If a test FAILs, fix the modal handlers minimally.

- [ ] **Step 3: Lint + format**

Run: `uv run --no-sync ruff check tests/unit/tui_textual/test_inspector.py && uv run --no-sync ruff format tests/unit/tui_textual/test_inspector.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/tui_textual/test_inspector.py redrobin/tui/textual_ui/inspector.py
git commit -m "test(tui): cover inspector filter input + group toggle"
```

---

## Task 5: Copy-row + wire the `i` open binding into the app

**Files:**
- Modify: `redrobin/tui/textual_ui/app.py`
- Test: `tests/unit/tui_textual/test_inspector.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_app_i_opens_inspector_with_snapshot():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        # seed a snapshot via the registry, then open the inspector
        from redrobin.tui.textual_ui.app import REGISTRY

        REGISTRY.dispatch(
            target=app,
            frame={"type": "working_memory_snapshot", "payload": SNAP},
        )
        await pilot.press("i")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"
        # it rendered the seeded engagement
        from textual.widgets import OptionList as _OL

        content = app.screen.query_one("#inspector-content", _OL)
        assert any("e2e-verify" in str(content.get_option_at_index(i).prompt)
                   for i in range(content.option_count))


@pytest.mark.asyncio
async def test_inspector_copy_row_copies_highlighted(monkeypatch):
    app = _Host(SNAP)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        copied: list[str] = []
        monkeypatch.setattr(app, "copy_to_clipboard", lambda text: copied.append(text))
        content = screen.query_one("#inspector-content", OptionList)
        content.highlighted = 0
        await pilot.pause()
        screen.query_one("#inspector-content", OptionList).focus()
        await pilot.press("y")
        await pilot.pause()
        assert copied and copied[0] == str(content.get_option_at_index(0).prompt)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -k "i_opens_inspector or copy_row" -v`
Expected: FAIL — the `i` binding/`action_inspect` doesn't exist on `RedRobinApp` (the `copy_row` test may also fail if focus/`y` doesn't reach the binding).

- [ ] **Step 3: Wire the `i` binding into `RedRobinApp` (`app.py`)**

Add the import (with the other panel imports — keep it on its own line for clarity):

```python
from redrobin.tui.textual_ui.inspector import InspectorScreen
```

Change `BINDINGS`:

```python
    BINDINGS = [("q", "quit", "Quit"), ("i", "inspect", "Inspector")]
```

Add the action method (place it near `on_mount`):

```python
    def action_inspect(self) -> None:
        """Open the Inspector modal over the current working-memory snapshot."""
        self.push_screen(InspectorScreen(self.snapshot))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/tui_textual/test_inspector.py -v`
Expected: PASS (all inspector tests). Also confirm the app suites still pass: `uv run --no-sync pytest tests/unit/tui_textual/ -v`.

- [ ] **Step 5: Lint + format**

Run: `uv run --no-sync ruff check redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_inspector.py && uv run --no-sync ruff format redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_inspector.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add redrobin/tui/textual_ui/app.py tests/unit/tui_textual/test_inspector.py
git commit -m "feat(tui): 'i' opens the Inspector modal + copy-row to clipboard"
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
Expected: ruff clean; format reports all files already formatted; import-linter `4 kept, 0 broken`; pytest all green (Phase 4 adds ~16 tests). If `ruff format --check .` flags anything, run `ruff format .` and re-run the gates.

- [ ] **Step 2: Mark Phase 4 done in `docs/superpowers/TODO.md`**

Change the Phase 4 line from `- [ ]` to `- [x]` with a one-line outcome (fill `N`/`M` from Step 1):

```markdown
- [x] **Phase 4 — Inspector `ModalScreen`** ✅ — `i` opens / `Esc` closes; left section nav with counts; filter `Input`; group-by kind|beacon for findings; native scroll (`OptionList`); copy-row (`y`). dict-based section helpers ported from the legacy inspector. *(branch `tui/phase-4-inspector`; N new tests; M suite green. Open key is `i` not `^I` — Ctrl+I == Tab in terminals.)*
```

Also update the "Now / Next" line to point at Phase 5.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/TODO.md
git commit -m "docs: mark TUI Phase 4 (Inspector) complete"
```

---

## Self-Review (completed while writing)

**1. Spec coverage (§3.3):**
- `^I` opens / `Esc` closes → **`i`** opens (Ctrl+I==Tab rationale documented) / `Esc` closes (Tasks 3, 5). ✓
- Left section nav with counts → `OptionList` nav populated from `section_counts` (Task 3). ✓
- Filter/search narrows the active section → `Input` + `on_input_changed` + `render_section(filter_text=)` (Tasks 1, 4). ✓
- Group-by kind|beacon → `g` cycles `None→kind→beacon`, `_grouped_findings` (Tasks 1/2/4). *(Collapsible counts deferred — v1 uses non-collapsible sub-headers; noted.)*
- Native scroll + scrollbar → `OptionList` scrolls natively (handles 148 findings). ✓
- Copy row (`y`) → `action_copy_row` → `copy_to_clipboard(highlighted prompt)` (Task 5). ✓

**2. Placeholder scan:** none — every step has concrete code/commands + expected output.

**3. Type/name consistency:** `SECTIONS`, `section_counts`, `render_section`, `_grouped_findings`, `InspectorScreen`, ids (`#inspector`, `#inspector-nav`, `#inspector-content`, `#inspector-filter`, `#inspector-title`, `#inspector-hint`, `#inspector-main`, `#inspector-body`), `action_close/cycle_group/copy_row`, `action_inspect`, binding `i` — used identically across tasks. Tests query the modal via `app.screen.query_one` (per the verified Pilot behavior). `OptionList.OptionHighlighted` handler is `on_option_list_option_highlighted` with `.option_list`/`.option_id`.

---

## Execution Notes

- **Operator smoke (after Task 6, before PR):** live daemon → `python -m redrobin.tui.textual_ui.app` → press **`i`** → the Inspector opens over the dimmed dashboard. Verify: section nav with counts (FINDINGS shows ~150+), arrow-navigate sections, type in the filter to narrow (e.g. `kerberos`), press `g` to group findings by kind then beacon, `↑↓` + `y` copies a row (paste elsewhere to confirm), `Esc` closes back to the dashboard.
- **PR:** `tui/phase-4-inspector` → `dev-juan` (CI runs on the merge push). Then Phase 5 (navigation: Command Palette providers + `BINDINGS` + `?` help + Tab focus-cycling).
