"""redrobin/application/chat/recorder.py — the chat transcript's single writer.

A bus consumer with the same shape as ``WorkingMemoryReducer``/``Assistant``: subscribe,
loop, dispatch, exit on the ``_close`` sentinel. It maps the assistant-stream events to
:class:`~redrobin.domain.chat.ChatTurn` rows and appends them, so the conversation
survives a daemon restart and can be fed back into later cycles.

Never-crash: a failed append is logged and swallowed — the loop survives (a full disk
must not kill the chat, let alone the assistant). A turn's text is NEVER logged: it can
contain credentials the operator pasted or discussed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from redrobin.application.agents.assistant.interpret import is_own_proposal_question
from redrobin.application.event_bus import EventBus
from redrobin.domain.chat import ChatTurn
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def advisory_text(payload: dict[str, Any]) -> str:
    """Compose an advisory's stored text from its wire payload.

    The application layer cannot reuse the TUI renderer (import-linter: application
    never imports presentation), so this mirrors the operator-facing shape — the action
    plus why/opsec/conf — minus refs, which were dropped from the output on 2026-07-24.
    Absent fields are omitted rather than rendered empty. Every field is STRIPPED, matching
    the dialogue path (``turn_for`` strips at extraction): these values come off a model
    reply, so trailing newlines and indentation are routine, and an unstripped one would be
    baked into the stored turn and re-injected into every later prompt.
    """
    lines = [str(payload.get("suggested_action") or "").strip()]
    for label, key in (("why", "rationale"), ("opsec", "opsec_risk"), ("conf", "confidence")):
        value = str(payload.get(key) or "").strip()
        if value:
            lines.append(f"{label}    {value}")
    return "\n".join(line for line in lines if line)


def turn_for(event_type: str, payload: dict[str, Any], *, ts: str) -> ChatTurn | None:
    """Map one bus event to a ChatTurn, or None when it is not part of the chat.

    The advisory/heads-up split uses the SAME discriminator the TUI renders with (a
    ``correlation_id`` means a command triggered it), so a stored turn and its chat line
    can never disagree. Pure; never raises.
    """
    from_operator = payload.get("source") == "operator"
    # Stripped at extraction: a whitespace-only message is not a turn, and letting one
    # through would burn a slot of the dialogue quota on nothing.
    text = str(payload.get("text") or "").strip()

    if event_type == "operator.question":
        if from_operator:
            return ChatTurn(ts=ts, role="operator", kind="question", text=text) if text else None
        if is_own_proposal_question(payload):
            return ChatTurn(ts=ts, role="assistant", kind="confirm", text=text) if text else None
        return None

    if event_type == "operator.answer":
        if not text:
            return None
        role = "operator" if from_operator else "assistant"
        return ChatTurn(ts=ts, role=role, kind="answer", text=text)

    if event_type == "advisor.recommendation":
        # Guard on the ACTION, not the composed body: with an empty suggested_action the
        # body can still be non-empty (rationale/opsec/conf compose into "why  x"), which
        # would record an advisory that advises nothing.
        if not str(payload.get("suggested_action") or "").strip():
            return None
        body = advisory_text(payload)
        correlation_id = payload.get("correlation_id")
        return ChatTurn(
            ts=ts,
            role="assistant",
            kind="advisory" if correlation_id else "heads_up",
            text=body,
            correlation_id=str(correlation_id) if correlation_id else None,
        )

    return None


class ChatRecorder:
    """Appends the assistant stream to the chat transcript (the store's single writer)."""

    def __init__(
        self,
        *,
        bus: EventBus,
        repo: ChatHistoryRepo,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._now = now
        # Optional readiness callback, mirroring the reducer/assistant: the lifespan and
        # tests await it so no early event is missed.
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("chat recorder on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                self._record(evt)
        finally:
            await self._bus.unsubscribe(sub)

    def _record(self, evt: dict[str, Any]) -> None:
        """Map + append one event. Never raises — a write failure must not kill the loop."""
        try:
            payload = evt.get("payload")
            turn = turn_for(
                str(evt.get("type") or ""),
                payload if isinstance(payload, dict) else {},
                # Normalised to UTC so the ORDERING CONTRACT is structural: ts is sorted
                # lexicographically, which is only chronological while every stamp carries
                # the same offset. The default clock is already UTC; this keeps a future
                # injected clock (naive or offset-carrying) from breaking the sort. Note a
                # naive datetime is assumed to be LOCAL time here — astimezone's documented
                # behaviour, and still a uniform +00:00 offset on the wire.
                ts=self._now().astimezone(timezone.utc).isoformat(),
            )
            if turn is None:
                return
            self._repo.append(turn)
        except Exception:
            # NEVER log the turn's text — it can contain credentials.
            logger.exception("chat recorder: failed to append a turn to %s", self._repo.path)


__all__ = ["ChatRecorder", "advisory_text", "turn_for"]
