"""RedRobinApp — the Textual TUI client shell (Phase 1 foundation).

Holds reactive ``snapshot``/``events`` updated by frame handlers, composes the
layout (topbar + three panes + reserved advisor dock + footer), and starts the
WS worker on mount. Panel content is intentionally minimal here — Phase 2 fills
the panes with DataTable/RichLog widgets.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
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

    def __init__(
        self,
        *,
        start_worker: bool = True,
        ws_url: str | None = None,
        token: str | None = None,
    ) -> None:
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

    def watch_snapshot(self, snapshot: dict) -> None:
        """Re-render the topbar when the snapshot changes. No invalidate() —
        Textual repaints bound widgets automatically (this is why #155 cannot
        recur: there is no manual repaint path to miss)."""
        engagement = snapshot.get("engagement_id") or "-"
        try:
            self.query_one("#topbar", Static).update(f"Red Robin · {engagement} · ●")
        except NoMatches:
            pass  # snapshot set before mount; compose() seeds the static text


@REGISTRY.register("working_memory_snapshot")
def _on_snapshot(app: "RedRobinApp", payload: dict) -> None:
    app.snapshot = payload  # reactive assignment → watch_snapshot fires


@REGISTRY.register("opsec.verdict")
def _on_verdict(app: "RedRobinApp", payload: dict) -> None:
    # Reassign (not append) so the reactive watcher sees the change.
    app.events = app.events + [payload]
