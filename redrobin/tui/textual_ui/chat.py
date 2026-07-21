"""Assistant Chat pane (#28) — the operator's ask/answer surface + inline pending
confirms. Replaces the center Verdicts/Events pane (operator decision 2026-07-21).
Renderers are pure Rich (unit-testable, like advisor.format_recommendation).

The frame handlers (register_chat/_on_answer/_on_question) + Input routing
(next_operator_message: ask vs an explicit /y /n confirm) live here too; the
handlers are bound onto the shared REGISTRY from app.py via register_chat."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog

from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.tui.textual_ui.frames import Registry

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

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        kind, msg = next_operator_message(text, pending=self._pending, q_index=self._q_counter)
        self.append_line(format_operator_echo(text))
        sent = await self.app.send_operator_message(msg)  # type: ignore[attr-defined]
        # A question bumps the monotonic local counter unconditionally (harmless on a failed
        # send). A confirm answer consumes the pending it targeted ONLY on a confirmed send —
        # otherwise a re-typed /y after reconnect must still be able to resolve it (#28 #2).
        if kind == "question":
            self._q_counter += 1
        elif sent and msg["question_id"] in self._pending:
            self._pending.remove(msg["question_id"])


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


def is_confirm_question(payload: dict[str, Any]) -> bool:
    refs = payload.get("context_refs")
    return isinstance(refs, list) and any(str(r).startswith("milestone:") for r in refs)


def _milestone_id(payload: dict[str, Any]) -> str:
    for r in payload.get("context_refs") or []:
        if str(r).startswith("milestone:"):
            return str(r).split(":", 1)[1]
    return ""


def next_operator_message(
    text: str, *, pending: list[str], q_index: int
) -> tuple[str, dict[str, Any]]:
    """Route the operator's submitted text. A confirm answer is ONLY an EXPLICIT slash
    token (/y /yes /n /no) while a confirm is pending — so an ordinary short word
    ('ok', 'no creds?') is NEVER mistaken for a confirm and can never silently commit/
    drop engagement state. Everything else is a question with a minted id. The daemon's
    resolve_confirm still authorizes the commit/drop; the slash token makes routing
    collision-free. The minted id also never collides with a semantic milestone id — this is
    now ENFORCED, not merely likely: minted ids carry the reserved OPERATOR_QUESTION_ID_PREFIX,
    which parse_interpretation forbids in a milestone_id (the two namespaces are disjoint)."""
    t = text.strip()
    low = t.lower()
    if pending and low in ("/y", "/yes", "/n", "/no"):
        answer = "yes" if low in ("/y", "/yes") else "no"
        return "answer", {"type": "operator.answer", "question_id": pending[0], "text": answer}
    return "question", {
        "type": "operator.question",
        "question_id": f"{OPERATOR_QUESTION_ID_PREFIX}{q_index}",
        "text": t,
    }


def _chat(app: Any) -> "ChatPane | None":
    from textual.css.query import NoMatches

    try:
        return app.query_one("#events", ChatPane)  # the ChatPane IS the #events pane
    except NoMatches:
        return None  # pre-mount — mirror advisor._on_recommendation's guard (never-crash)


def _on_answer(app: Any, payload: dict[str, Any]) -> None:
    if payload.get("source") == "operator":
        return  # our own echo fanned back — don't re-render
    pane = _chat(app)
    if pane is not None:
        pane.append_line(format_answer(payload))


def _on_question(app: Any, payload: dict[str, Any]) -> None:
    if payload.get("source") == "operator" or not is_confirm_question(payload):
        return  # a plain operator ask (its own echo) — skip; only assistant confirms render
    pane = _chat(app)
    if pane is None:
        return
    pane.append_line(format_confirm(payload))
    mid = _milestone_id(payload)
    if mid and mid not in pane._pending:
        pane._pending.append(mid)


def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)


__all__ = [
    "ChatPane",
    "format_answer",
    "format_confirm",
    "format_operator_echo",
    "is_confirm_question",
    "next_operator_message",
    "register_chat",
]
