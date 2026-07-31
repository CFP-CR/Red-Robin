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
        ("Focus Assistant Chat", _focus("#chat-input"), "Focus the Assistant Chat input"),
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
