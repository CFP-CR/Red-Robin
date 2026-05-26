"""RedRobinApp — the Textual TUI client shell (Phase 1 foundation).

Holds reactive ``snapshot``/``events`` updated by frame handlers, composes the
layout (topbar + three panes + reserved advisor dock + footer), and starts the
WS worker on mount. Panel content is intentionally minimal here — Phase 2 fills
the panes with DataTable/RichLog widgets.
"""

from __future__ import annotations

import asyncio
import os

from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Container, Horizontal
from textual.css.query import NoMatches
from textual.events import Resize
from textual.reactive import reactive
from textual.widgets import Footer, Static

from redrobin.tui.textual_ui.frames import Registry
from redrobin.tui.textual_ui.inspector import InspectorScreen
from redrobin.tui.textual_ui.panels import (
    EventsLog,
    MemoryPanel,
    SessionsTable,
    TopBar,
    apply_verdict,
    verdict_counts,
)

# Module-level registry: handlers register at import time (Task 5). A future
# feature adds its frame type with a new @REGISTRY.register — no edits here.
REGISTRY = Registry()


def is_too_small(width: int, height: int) -> bool:
    """The dashboard needs at least 80x24 to lay out the three panes legibly."""
    return width < 80 or height < 24


class RedRobinApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "Red Robin"
    BINDINGS = [("q", "quit", "Quit"), ("i", "inspect", "Inspector")]

    snapshot: reactive[dict] = reactive(dict)
    events: reactive[list] = reactive(list)
    connection_state: reactive[str] = reactive("disconnected")

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
        yield TopBar("Red Robin", id="topbar")
        with Horizontal(id="main"):
            with Container(id="sessions") as sessions_box:
                sessions_box.border_title = "Sessions"
                yield SessionsTable(id="sessions-table")
            with Container(id="events") as events_box:
                events_box.border_title = "Verdicts / Events"
                yield EventsLog(
                    id="events-log", highlight=False, markup=False, wrap=True, min_width=0
                )
            with Container(id="memory") as memory_box:
                memory_box.border_title = "Working Memory"
                yield MemoryPanel(id="memory-panel")
        yield Container(id="advisor-dock")  # reserved seam (hidden via .tcss)
        yield Static("Terminal too small — resize to at least 80x24.", id="resize-hint")
        yield Footer()

    def watch_snapshot(self, snapshot: dict) -> None:
        """Re-render topbar + Sessions + Working Memory on snapshot change. No
        manual repaint — reactive assignment drives Textual's compositor (#155)."""
        self._refresh_topbar()
        try:
            self.query_one("#sessions-table", SessionsTable).update_from_snapshot(snapshot)
            self.query_one("#memory-panel", MemoryPanel).update_from_snapshot(snapshot)
        except (NoMatches, ScreenStackError):
            pass  # pre-mount (no screen yet) or widgets not mounted — compose seeds them

    def watch_events(self, events: list) -> None:
        """Re-render the verdict/event log + header counts whenever events change."""
        self._refresh_topbar()
        try:
            self.query_one("#events-log", EventsLog).render_events(events)
        except (NoMatches, ScreenStackError):
            pass

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

    def action_inspect(self) -> None:
        """Open the Inspector modal over the current working-memory snapshot."""
        self.push_screen(InspectorScreen(self.snapshot))

    def on_mount(self) -> None:
        """Start the WS worker unless disabled (tests pass start_worker=False)."""
        if not self._start_worker:
            return
        from redrobin.tui.textual_ui.ws_worker import ws_loop

        self._stop_event = asyncio.Event()
        url = self._ws_url or os.environ.get("REDROBIN_DAEMON_WS", "ws://127.0.0.1:8765/v1/tui")
        self.run_worker(
            ws_loop(
                target=self,
                url=url,
                token=self._token,
                registry=REGISTRY,
                stop_event=self._stop_event,
                set_state=self._set_connection_state,
            ),
            name="ws",
            exclusive=True,
            # A fatal connect error (e.g. the daemon closes /v1/tui with 1008 on a
            # bad/missing token) must NOT tear down the app. Keep it alive; Phase 3
            # surfaces connection state in the topbar status dot.
            exit_on_error=False,
        )

    def on_unmount(self) -> None:
        stop = getattr(self, "_stop_event", None)
        if stop is not None:
            stop.set()

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


@REGISTRY.register("working_memory_snapshot")
def _on_snapshot(app: "RedRobinApp", payload: dict) -> None:
    app.snapshot = payload  # reactive assignment → watch_snapshot fires


@REGISTRY.register("opsec.verdict")
def _on_verdict(app: "RedRobinApp", payload: dict) -> None:
    # Reassign (not append) so watch_events fires; apply_verdict handles the
    # Layer B in-place correction by correlation_id.
    app.events = apply_verdict(app.events, payload)


def main() -> None:
    """Dev entry: ``python -m redrobin.tui.textual_ui.app``. The CLI entrypoint
    (`redrobin tui`) is swapped to this in Phase 6."""
    from redrobin.application.transport_auth import get_or_create_token

    try:
        token = get_or_create_token()
    except Exception:
        token = None
    RedRobinApp(token=token).run()


if __name__ == "__main__":
    main()
