"""redrobin/tui/app.py — Main full-screen TUI app (prompt_toolkit).

3-panel layout per spec §4 Componente 5 + mockup at `_Claude/redrobin-mockup/`:
    +----------------+---------------------+------------+
    |  Working       |  Event Stream       |  Chat      |
    |  Memory        |                     |            |
    |  (left)        |  (center)           |  (right)   |
    +----------------+---------------------+------------+
    |  Status bar: engagement + phase + adapter status   |
    +-----------------------------------------------------+

For v0.1: layout + input handling + slash command parsing.
WebSocket bridge to daemon is wired in Task 39 (CLI integration).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from redrobin.tui.i18n import Labels, load_labels


@dataclass
class TUIState:
    """In-memory TUI state. Updated by WebSocket events from daemon (Task 39)."""

    working_memory_text: str = "(loading working memory...)"
    event_stream_text: str = "(no events yet)"
    chat_text: str = "(chat empty — type to ask Red Robin)"
    status_bar_text: str = "no engagement | adapter: disconnected"


def _panel_window(get_text: Callable[[], str], title: str) -> HSplit:
    """A pane with a title bar + body text."""
    title_window = Window(
        content=FormattedTextControl(text=lambda: f" {title} "),
        height=1,
        style="class:panel-title",
    )
    body_window = Window(
        content=FormattedTextControl(text=get_text),
        wrap_lines=True,
        style="class:panel-body",
    )
    return HSplit([title_window, body_window])


def build_app(state: TUIState, labels: Labels) -> Application:
    """Construct the prompt_toolkit Application."""
    input_buffer = Buffer(multiline=False)

    working_memory_pane = _panel_window(
        lambda: state.working_memory_text, labels.tui.working_memory
    )
    event_stream_pane = _panel_window(lambda: state.event_stream_text, labels.tui.event_stream)
    chat_pane = _panel_window(lambda: state.chat_text, labels.tui.chat)

    main_panels = VSplit(
        [
            HSplit([working_memory_pane], width=D(weight=2)),
            HSplit([event_stream_pane], width=D(weight=3)),
            HSplit([chat_pane], width=D(weight=2)),
        ]
    )

    status_bar = Window(
        content=FormattedTextControl(text=lambda: state.status_bar_text),
        height=1,
        style="class:status-bar",
    )

    input_field = Window(
        content=BufferControl(buffer=input_buffer),
        height=1,
        style="class:input",
    )

    root = HSplit([main_panels, status_bar, input_field])

    style = Style.from_dict(
        {
            "panel-title": "reverse bold",
            "panel-body": "",
            "status-bar": "reverse",
            "input": "",
        }
    )

    kb = KeyBindings()

    @kb.add("c-c")
    @kb.add("c-q")
    def _exit(event: object) -> None:
        event.app.exit()  # type: ignore[attr-defined]

    return Application(
        layout=Layout(root),
        key_bindings=kb,
        full_screen=True,
        style=style,
    )


def run() -> None:
    """Entry point — `redrobin tui` invokes this."""
    state = TUIState()
    labels = Labels(load_labels())
    app = build_app(state, labels)
    app.run()
