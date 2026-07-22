"""Assistant Chat pane (#28) — the operator's ask/answer surface + inline pending
confirms. Replaces the center Verdicts/Events pane (operator decision 2026-07-21).
Renderers are pure Rich (unit-testable).

The frame handlers (register_chat/_on_answer/_on_question) + Input routing
(next_operator_message: ask vs an explicit /y /n confirm) live here too; the
handlers are bound onto the shared REGISTRY from app.py via register_chat."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Input, RichLog, Static

from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.tui.textual_ui.frames import Registry

_ACCENT = "#58a6ff"
_MUTED = "#8b949e"
_CONFIRM = "#e3b341"
_RISK_COLORS = {"INFO": "#3fb950", "WARN": "#e3b341", "DANGER": "#f85149", "ESCALATE": "#58a6ff"}

_PLACEHOLDER_IDLE = "Ask the assistant…  (/y or /n to a confirm)"
_PLACEHOLDER_CONFIRM = "▶ Confirm pending — reply /y or /n"


class ChatPane(Container):
    """A scrollback (RichLog) + an Input. `border_title` set by app.compose."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending: list[str] = []
        self._pending_prompts: dict[str, str] = {}  # milestone_id -> its confirm question text
        self._q_counter = 0

    def compose(self) -> ComposeResult:
        log = RichLog(
            id="chat-log", highlight=False, markup=False, wrap=True, min_width=0, max_lines=2000
        )
        log.can_focus = False  # only the Input is a tab stop (the scrollback isn't interactive)
        yield log
        with Vertical(id="chat-bottom"):
            bar = Static("", id="confirm-bar")  # reserved; revealed only while a confirm is pending
            bar.can_focus = False
            bar.display = False  # start hidden (harness loads no CSS)
            bar.border_title = "Red Robin needs your call"
            yield bar
            yield Input(id="chat-input", placeholder=_PLACEHOLDER_IDLE)

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
        # otherwise a re-typed /y after reconnect must still be able to resolve it (#28 #2) —
        # and drops a durable one-line trace of the decision into the chat (OD-1).
        if kind == "question":
            self._q_counter += 1
        elif sent and msg["question_id"] in self._pending:
            resolved = msg["question_id"]
            self._pending.remove(resolved)
            self._pending_prompts.pop(resolved, None)
            self.append_line(format_resolution(resolved, msg["text"] == "yes"))
        self._refresh_confirm_bar()

    def _refresh_confirm_bar(self) -> None:
        """Reveal/hide the prominent confirm bar + flip the input placeholder from the current
        _pending. The bar reflects _pending[0] — the confirm a /y or /n resolves. Never-crash
        (pre-mount safe: a query before mount is swallowed)."""
        try:
            bar = self.query_one("#confirm-bar", Static)
            inp = self.query_one("#chat-input", Input)
        except Exception:
            return
        if self._pending:
            head = self._pending[0]
            bar.update(format_confirm_bar(self._pending_prompts.get(head, head)))
            bar.display = True
            inp.placeholder = _PLACEHOLDER_CONFIRM
        else:
            bar.display = False
            inp.placeholder = _PLACEHOLDER_IDLE


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


def format_recommendation(payload: dict[str, Any]) -> Text:
    """The 5-part advisory as a chat message (one voice). The provenance tag is
    driven by correlation_id: reactive (a command triggered it) carries
    '<beacon>:<command>'; proactive (self-triggered) carries None -> 'heads-up'."""
    action = str(payload.get("suggested_action") or "")
    rationale = str(payload.get("rationale") or "")
    risk = str(payload.get("opsec_risk") or "INFO")
    confidence = str(payload.get("confidence") or "")
    references = payload.get("references") or []
    tag = "advisory" if payload.get("correlation_id") else "heads-up"
    t = Text()
    t.append("Red Robin", style=f"bold {_ACCENT}")
    t.append(f" · {tag}", style=_MUTED)
    t.append("  (nothing dispatched)\n", style=_MUTED)
    t.append("→ ", style="bold")
    t.append(f"{action}\n", style="bold")
    t.append("why    ", style=_MUTED)
    t.append(f"{rationale}\n")
    t.append("opsec  ", style=_MUTED)
    t.append(f"{risk}\n", style=f"bold {_RISK_COLORS.get(risk, 'white')}")
    t.append("conf   ", style=_MUTED)
    t.append(f"{confidence}\n")
    t.append("refs   ", style=_MUTED)
    t.append(", ".join(str(r) for r in references) if references else "(none)")
    return t


def format_confirm_bar(question: str) -> Text:
    """The prominent pending-confirm content, rendered inside #confirm-bar (which carries the
    'Red Robin needs your call' border title). Distinct from a chat line: the question, then
    the explicit /y /n affordance."""
    t = Text()
    t.append(question or "Confirm?", style=f"bold {_CONFIRM}")
    t.append("     ")
    t.append("/y", style="bold #3fb950")
    t.append(" yes   ", style=_MUTED)
    t.append("/n", style="bold #f85149")
    t.append(" no", style=_MUTED)
    return t


def format_resolution(milestone_id: str, confirmed: bool) -> Text:
    """A durable one-line trace of a resolved confirm in the chat log (OD-1). The resulting
    milestone status shows in the Working-Memory panel; this records the operator's call."""
    t = Text()
    if confirmed:
        t.append("✓ ", style="bold #3fb950")
        t.append(f"{milestone_id} confirmed", style="#3fb950")
    else:
        t.append("✗ ", style="bold #f85149")
        t.append(f"{milestone_id} declined", style="#f85149")
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
        return None  # pre-mount — never-crash guard (mirrors _on_answer/_on_question)


def _on_recommendation(app: Any, payload: dict[str, Any]) -> None:
    pane = _chat(app)
    if pane is not None:
        pane.append_line(format_recommendation(payload))


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
    mid = _milestone_id(payload)
    if mid and mid not in pane._pending:
        pane._pending.append(mid)
    if mid:
        pane._pending_prompts[mid] = str(payload.get("text") or "")
    pane._refresh_confirm_bar()  # reveal the prominent bar + flip the placeholder


def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)
    registry.register("advisor.recommendation")(_on_recommendation)


__all__ = [
    "ChatPane",
    "format_answer",
    "format_confirm_bar",
    "format_operator_echo",
    "format_recommendation",
    "format_resolution",
    "is_confirm_question",
    "next_operator_message",
    "register_chat",
]
