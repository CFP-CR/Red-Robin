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
