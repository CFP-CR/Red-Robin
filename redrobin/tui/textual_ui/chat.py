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
from textual.timer import Timer
from textual.widgets import Input, RichLog, Static

from redrobin.domain.assistant import OPERATOR_QUESTION_ID_PREFIX
from redrobin.domain.chat import VOLUNTEERED_KINDS
from redrobin.tui.textual_ui.frames import Registry

# The pane's whole palette. Every colour in this module comes from here — the same green
# means "yes/low risk" and the same red means "no/high risk" wherever they appear.
_ACCENT = "#58a6ff"
_MUTED = "#8b949e"
_CONFIRM = "#e3b341"
_SUCCESS = "#3fb950"
_DANGER = "#f85149"
_RISK_COLORS = {
    "INFO": _SUCCESS,
    "WARN": _CONFIRM,
    "DANGER": _DANGER,
    "ESCALATE": _ACCENT,
}  # keys = the Severity enum (advisory rendering depends on all four)

# A volunteered turn (advisory / heads-up) and a confirm carry their kind in the label:
# the speaker alone does not say whether Red Robin was answering or speaking up. A plain
# question or answer needs no tag. This WIDENS the LLM slice's rule in
# application/chat/history.py, which tags only VOLUNTEERED_KINDS — a confirm needs
# distinguishing from an answer on screen in a way the model does not need in prose.
_TAGGED_KINDS = VOLUNTEERED_KINDS | {"confirm"}

_PLACEHOLDER_IDLE = "Ask the assistant…  (/y or /n to a confirm)"
_PLACEHOLDER_CONFIRM = "▶ Confirm pending — reply /y or /n"

_THINK_DELAY = 0.2  # show only after 200ms — avoid a flash on fast cycles (TUI best practice)
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _thinking_text(frame: int) -> Text:
    """The transient working-indicator line: a braille spinner glyph + a generic label."""
    glyph = _SPIN_FRAMES[frame % len(_SPIN_FRAMES)]
    t = Text()
    t.append(f"{glyph} ", style=_ACCENT)
    t.append("Red Robin is thinking…", style=_MUTED)
    return t


class ChatPane(Container):
    """A scrollback (RichLog) + an Input. `border_title` set by app.compose."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending: list[str] = []
        self._pending_prompts: dict[str, str] = {}  # milestone_id -> its confirm question text
        self._q_counter = 0
        self._thinking = 0  # active LLM-cycle count (start/stop are balanced, may nest)
        self._think_timer: Timer | None = None  # the 200ms delayed-reveal handle
        self._spin_timer: Timer | None = None  # the glyph animation interval
        self._spin_i = 0
        self._hydrated = False  # chat_history is replayed on every reconnect; render it once

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
            status = Static("", id="thinking-status")  # reserved; revealed 200ms into a cycle
            status.can_focus = False
            status.display = False  # start hidden (harness loads no CSS) — mirrors the bar
            yield status
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

    def start_thinking(self) -> None:
        """A cycle began. Arm the 200ms delayed reveal; idempotent while already armed/shown
        (nesting-safe — balanced with stop_thinking). Never-crash."""
        self._thinking += 1
        if self._thinking == 1 and self._think_timer is None:
            try:
                self._think_timer = self.set_timer(_THINK_DELAY, self._reveal_thinking)
            except Exception:
                pass

    def stop_thinking(self) -> None:
        """A cycle ended. When no cycle remains, cancel a still-pending reveal, hide the
        spinner, and stop the animation. Idempotent (a stop with no start is a no-op)."""
        if self._thinking > 0:
            self._thinking -= 1
        if self._thinking > 0:
            return
        self._cancel_timer("_think_timer")
        self._cancel_timer("_spin_timer")
        try:
            self.query_one("#thinking-status", Static).display = False
        except Exception:
            pass

    def _reveal_thinking(self) -> None:
        """Fired 200ms after the first start. If a cycle is still running, reveal the spinner
        and start the glyph animation; if it already finished, do nothing (no flash)."""
        self._think_timer = None
        if self._thinking <= 0:
            return  # the cycle finished within the 200ms window — never reveal
        try:
            status = self.query_one("#thinking-status", Static)
        except Exception:
            return
        self._spin_i = 0
        status.update(_thinking_text(self._spin_i))
        status.display = True
        try:
            self._spin_timer = self.set_interval(0.1, self._tick_thinking)
        except Exception:
            pass

    def _tick_thinking(self) -> None:
        """Advance the spinner glyph (display-only)."""
        self._spin_i += 1
        try:
            self.query_one("#thinking-status", Static).update(_thinking_text(self._spin_i))
        except Exception:
            pass

    def _cancel_timer(self, attr: str) -> None:
        """Stop + clear a stored Timer handle (self._think_timer / self._spin_timer)."""
        timer = getattr(self, attr, None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            setattr(self, attr, None)


def format_operator_echo(text: str) -> Text:
    t = Text()
    t.append("You  ", style=f"bold {_ACCENT}")
    t.append(text)
    return t


def format_answer(payload: dict[str, Any]) -> Text:
    t = Text()
    t.append("Red Robin  ", style=f"bold {_ACCENT}")
    t.append(str(payload.get("text") or ""))
    return t


def format_recommendation(payload: dict[str, Any]) -> Text:
    """The 4-part advisory as a chat message (one voice). The provenance tag is
    driven by correlation_id: reactive (a command triggered it) carries
    '<beacon>:<command>'; proactive (self-triggered) carries None -> 'heads-up'.
    Refs/grounding are intentionally not rendered (operator UX, 2026-07-22)."""
    action = str(payload.get("suggested_action") or "")
    rationale = str(payload.get("rationale") or "")
    risk = str(payload.get("opsec_risk") or "INFO")
    confidence = str(payload.get("confidence") or "")
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
    t.append(f"{confidence}")
    return t


def format_confirm_bar(question: str) -> Text:
    """The prominent pending-confirm content, rendered inside #confirm-bar (which carries the
    'Red Robin needs your call' border title). Distinct from a chat line: the question, then
    the explicit /y /n affordance."""
    t = Text()
    t.append(question or "Confirm?", style=f"bold {_CONFIRM}")
    t.append("     ")
    t.append("/y", style=f"bold {_SUCCESS}")
    t.append(" yes   ", style=_MUTED)
    t.append("/n", style=f"bold {_DANGER}")
    t.append(" no", style=_MUTED)
    return t


def format_history_turn(turn: dict[str, Any]) -> Text:
    """One hydrated transcript turn as a chat line (spec 2026-07-27).

    Deliberately the SAME shape as the live renderers — no dimming, no separator, no
    "earlier" marker. The thread just starts, the way a chat client resumes.

    One difference from live, and it is intended: a historical advisory's ``opsec``
    value is not coloured. ``ChatRecorder`` flattens an advisory to text, so the
    structured ``opsec_risk`` that drives ``_RISK_COLORS`` is not on disk. Colouring it
    would mean either re-parsing the stored text (fragile) or adding a field that only
    post-ship rows could carry (a history where some advisories are coloured and others
    are not, which reads as a bug).
    """
    label = "You" if turn.get("role") == "operator" else "Red Robin"
    kind = str(turn.get("kind") or "")
    t = Text()
    t.append(label, style=f"bold {_ACCENT}")
    if kind in _TAGGED_KINDS:
        t.append(f" · {kind.replace('_', '-')}", style=_MUTED)
    # A volunteered body is the multi-line action/why/opsec/conf block, so it begins on
    # its own line exactly as the live advisory does. Dialogue is one line after the label.
    t.append("\n" if kind in VOLUNTEERED_KINDS else "  ")
    t.append(str(turn.get("text") or ""))
    return t


def format_resolution(milestone_id: str, confirmed: bool) -> Text:
    """A durable one-line trace of a resolved confirm in the chat log (OD-1). The resulting
    milestone status shows in the Working-Memory panel; this records the operator's call."""
    t = Text()
    if confirmed:
        t.append("✓ ", style=f"bold {_SUCCESS}")
        t.append(f"{milestone_id} confirmed", style=_SUCCESS)
    else:
        t.append("✗ ", style=f"bold {_DANGER}")
        t.append(f"{milestone_id} declined", style=_DANGER)
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


def _on_chat_history(app: Any, payload: dict[str, Any]) -> None:
    """Hydrate the pane from the transcript the daemon sends on connect.

    ONE-SHOT. ``ws_worker`` auto-reconnects (``async for ws in connect(...)``), and every
    reconnect re-runs the endpoint from the top and re-sends this frame. The working-memory
    frame is idempotent because it REPLACES ``app.snapshot``; this log is append-only, so
    without the guard a ping timeout would re-append the whole thread below the live
    conversation.

    Known limitation of one-shot: turns recorded while the socket was down are not
    rendered on reconnect. They are on disk and in the assistant's own context, and the
    operator sees them by restarting the TUI. Rendering only turns newer than the last
    hydrated one was considered and rejected: a live-rendered turn carries no ``ts`` in
    the TUI, so the watermark could not be advanced for it, and the next reconnect would
    duplicate everything rendered live in between.

    Order is the daemon's (file order, oldest first) — never re-sorted. A malformed entry
    is skipped rather than losing the whole thread: rendering nine of ten turns beats none.
    """
    pane = _chat(app)
    if pane is None or pane._hydrated:
        return
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return
    pane._hydrated = True
    for turn in turns:
        if isinstance(turn, dict):
            pane.append_line(format_history_turn(turn))


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


def _on_thinking(app: Any, payload: dict[str, Any]) -> None:
    pane = _chat(app)
    if pane is None:
        return
    state = str(payload.get("state") or "")
    if state == "start":
        pane.start_thinking()
    elif state == "stop":
        pane.stop_thinking()


def register_chat(registry: Registry) -> None:
    registry.register("operator.answer")(_on_answer)
    registry.register("operator.question")(_on_question)
    registry.register("advisor.recommendation")(_on_recommendation)
    registry.register("assistant.thinking")(_on_thinking)
    registry.register("chat_history")(_on_chat_history)


__all__ = [
    "ChatPane",
    "format_answer",
    "format_confirm_bar",
    "format_history_turn",
    "format_operator_echo",
    "format_recommendation",
    "format_resolution",
    "is_confirm_question",
    "next_operator_message",
    "register_chat",
]
