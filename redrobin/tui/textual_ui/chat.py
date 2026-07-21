"""Assistant Chat pane (#28) — the operator's ask/answer surface + inline pending
confirms. Replaces the center Verdicts/Events pane (operator decision 2026-07-21).
Renderers are pure Rich (unit-testable, like advisor.format_recommendation).

The frame handlers (register_chat/_on_answer/_on_question) + Input routing land in
the next unit; this module is the widget + pure renderers + send-handle surface."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog

_ACCENT = "#58a6ff"
_MUTED = "#8b949e"
_CONFIRM = "#e3b341"


class ChatPane(Container):
    """A scrollback (RichLog) + an Input. `border_title` set by app.compose."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending: list[str] = []
        self._q_counter = 0

    def compose(self) -> ComposeResult:
        log = RichLog(id="chat-log", highlight=False, markup=False, wrap=True, min_width=0)
        log.can_focus = False  # only the Input is a tab stop (the scrollback isn't interactive)
        yield log
        yield Input(id="chat-input", placeholder="Ask the assistant…  (/y or /n to a confirm)")

    def append_line(self, text: Text) -> None:
        try:
            self.query_one("#chat-log", RichLog).write(text)
        except Exception:
            pass


def format_operator_echo(text: str) -> Text:
    t = Text()
    t.append("You  ", style=f"bold {_ACCENT}")
    t.append(text)
    return t


def format_answer(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("Red Robin  ", style=f"bold {_ACCENT}")
    t.append(str(payload.get("text") or ""))
    cites = payload.get("citations") or []
    if cites:
        t.append("\n  refs ", style=_MUTED)
        t.append(", ".join(str(c) for c in cites), style=_MUTED)
    return t


def format_confirm(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("CONFIRM  ", style=f"bold {_CONFIRM}")
    t.append(str(payload.get("text") or ""))
    t.append("   [reply /y or /n]", style=_MUTED)
    return t


__all__ = ["ChatPane", "format_answer", "format_confirm", "format_operator_echo"]
