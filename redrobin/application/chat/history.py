"""redrobin/application/chat/history.py — the chat slice the assistant reasons over.

Builds the injected reader the ``Assistant`` consumes: read the tail -> apply the
domain's two-window selection -> render one plain-text block. Returning a STRING makes
it a drop-in twin of ``summary_reader`` (the L5 seam), which keeps the agent's context
builders free of chat types and keeps the rendering unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from redrobin.domain.chat import (
    DIALOGUE_KINDS,
    VOLUNTEERED_KINDS,
    ChatTurn,
    select_context_turns,
)
from redrobin.infrastructure.persistence.chat_history_repo import (
    MAX_TAIL_LINES,
    ChatHistoryRepo,
)

logger = logging.getLogger(__name__)

# Per-turn size cap (a context-rot guard, exactly like ``cap_wm_snapshot``'s
# ``assistant_wm_max_items``). The selector bounds the turn COUNT only, and one turn is
# NOT small: an answer can run to ``assistant_max_tokens`` (~8 KB) and a question can be
# an arbitrary paste (operators paste beacon output to ask "what is this?"). Without this
# the injected slice would be byte-unbounded on EVERY reactive cycle. A module constant,
# not a Setting (YAGNI — same call as ``MAX_TAIL_LINES``).
_MAX_TURN_CHARS = 800


def render_turns(turns: list[ChatTurn]) -> str:
    """Render selected turns as the ``# Conversation so far`` body.

    Dialogue turns are labelled by role alone; a volunteered turn also carries its kind
    (``advisory`` / ``heads-up``) so the model can tell an answer it was asked for from
    something it offered unprompted — the same distinction the TUI shows the operator.

    Each turn's text is truncated at ``_MAX_TURN_CHARS`` so the whole block has a real
    byte ceiling (turn count x this cap), not just a turn-count ceiling.
    """
    lines: list[str] = []
    for turn in turns:
        label = turn.role
        if turn.kind in VOLUNTEERED_KINDS:
            label = f"{turn.role} · {turn.kind.replace('_', '-')}"
        lines.append(f"[{label}] {_clip(turn.text)}")
    return "\n".join(lines)


def _clip(text: str) -> str:
    """One turn's text, bounded. Marks the cut so the model knows it is not the whole turn."""
    if len(text) <= _MAX_TURN_CHARS:
        return text
    return text[:_MAX_TURN_CHARS] + " …[truncated]"


def build_history_reader(
    repo: ChatHistoryRepo, *, dialogue_max: int, volunteered_max: int
) -> Callable[[], str]:
    """A zero-arg reader returning the rendered chat slice, or "" when there is none.

    Never raises: a read failure degrades to "" so the cycle still runs (the assistant
    then simply has no conversation context, exactly as before this feature).
    """

    def _read() -> str:
        try:
            # The cap is passed explicitly so saturation is observable below.
            parsed_turns = repo.tail(max_lines=MAX_TAIL_LINES)
            turns = select_context_turns(
                parsed_turns, dialogue_max=dialogue_max, volunteered_max=volunteered_max
            )
            # Diagnostic for the one way the two-window guarantee can still be defeated: the
            # read is capped, so a tail saturated with volunteered turns starves the dialogue
            # quota even though older dialogue exists on disk. Rare (the cap sits far above
            # the quotas) but silent, so make a real occurrence findable. Text is NEVER
            # logged — counts only.
            #
            # The check is INEXACT in both directions. Accepted: this is a DEBUG breadcrumb,
            # not a guarantee, and the two-window rule itself is enforced by
            # ``select_context_turns``, not here.
            #   FALSE NEGATIVE — ``tail`` returns PARSED turns, and it drops malformed lines
            #   rather than backfilling them, so a genuinely saturated read that contains one
            #   garbage line comes back as MAX_TAIL_LINES-1 and stays quiet. Being exact
            #   would mean ``tail`` also returning the raw line count: API churn on the store
            #   for a debug line, so the miss is taken instead.
            #   FALSE POSITIVE — a file of exactly MAX_TAIL_LINES fully-parseable lines with
            #   no dialogue ANYWHERE trips both conditions and says "older dialogue is
            #   outside the read window" when there is no older dialogue to miss.
            if len(parsed_turns) >= MAX_TAIL_LINES:
                dialogue_selected = sum(1 for t in turns if t.kind in DIALOGUE_KINDS)
                if dialogue_selected < dialogue_max:
                    logger.debug(
                        "chat history: tail cap hit (%d lines) and only %d/%d dialogue turns "
                        "selected — older dialogue is outside the read window",
                        MAX_TAIL_LINES,
                        dialogue_selected,
                        dialogue_max,
                    )
        except Exception:
            logger.exception("chat history: read failed; proceeding without conversation context")
            return ""
        return render_turns(turns)

    return _read


__all__ = ["build_history_reader", "render_turns"]
