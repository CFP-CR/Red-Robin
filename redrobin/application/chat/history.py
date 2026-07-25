"""redrobin/application/chat/history.py — the chat slice the assistant reasons over.

Builds the injected reader the ``Assistant`` consumes: read the tail -> apply the
domain's two-window selection -> render one plain-text block. Returning a STRING makes
it a drop-in twin of ``summary_reader`` (the L5 seam), which keeps the agent's context
builders free of chat types and keeps the rendering unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from redrobin.domain.chat import VOLUNTEERED_KINDS, ChatTurn, select_context_turns
from redrobin.infrastructure.persistence.chat_history_repo import ChatHistoryRepo

logger = logging.getLogger(__name__)


def render_turns(turns: list[ChatTurn]) -> str:
    """Render selected turns as the ``# Conversation so far`` body.

    Dialogue turns are labelled by role alone; a volunteered turn also carries its kind
    (``advisory`` / ``heads-up``) so the model can tell an answer it was asked for from
    something it offered unprompted — the same distinction the TUI shows the operator.
    """
    lines: list[str] = []
    for turn in turns:
        label = turn.role
        if turn.kind in VOLUNTEERED_KINDS:
            label = f"{turn.role} · {turn.kind.replace('_', '-')}"
        lines.append(f"[{label}] {turn.text}")
    return "\n".join(lines)


def build_history_reader(
    repo: ChatHistoryRepo, *, dialogue_max: int, volunteered_max: int
) -> Callable[[], str]:
    """A zero-arg reader returning the rendered chat slice, or "" when there is none.

    Never raises: a read failure degrades to "" so the cycle still runs (the assistant
    then simply has no conversation context, exactly as before this feature).
    """

    def _read() -> str:
        try:
            turns = select_context_turns(
                repo.tail(), dialogue_max=dialogue_max, volunteered_max=volunteered_max
            )
        except Exception:
            logger.exception("chat history: read failed; proceeding without conversation context")
            return ""
        return render_turns(turns)

    return _read


__all__ = ["build_history_reader", "render_turns"]
