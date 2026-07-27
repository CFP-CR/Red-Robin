"""redrobin/domain/chat.py — the session chat thread (pure domain).

A :class:`ChatTurn` is one entry in the operator<->assistant conversation. This is a
NEW memory layer, distinct from the canonical five: L2 holds engagement *state*, L5
summarises *beacon activity*, L1/L3 hold *durable lessons*, and the Operational
Journal is the verbatim *command* transcript. None of them records the dialogue.

The two kind groups exist so the context selector can give each its own quota —
beacon-driven advisories must never evict the dialogue thread (spec §6).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    """One recorded turn of the conversation.

    ``ts`` is an ISO-8601 UTC timestamp at microsecond RESOLUTION (so ordering ties are
    practically impossible). Not always microsecond-WIDTH: ``datetime.isoformat`` omits the
    fractional field entirely when ``microsecond == 0``, so a stamp can read
    ``...T05:00:01+00:00``. Ordering is unaffected — ``'+'`` sorts below ``'.'`` and below
    every digit, so an abbreviated stamp sorts first within its second, which is where it
    belongs. Ordering is a lexicographic sort of that string, which is
    chronological ONLY while every ts carries the same UTC offset — guaranteed by the
    single-writer invariant (``ChatRecorder`` normalises its clock with
    ``.astimezone(timezone.utc)`` before stamping, whatever clock it was given).
    ``role`` is ``"operator"`` or ``"assistant"``;
    ``kind`` is one of the five values in the two frozensets below.
    ``correlation_id`` carries the reactive trigger handle (``"<beacon>:<command>"``)
    for an advisory, and is None otherwise.
    """

    ts: str
    role: str
    kind: str
    text: str
    correlation_id: str | None = None


# The operator<->assistant exchange: the operator asks, the assistant answers, and a
# confirm asks for the operator's decision. This is the thread that must never be lost.
DIALOGUE_KINDS = frozenset({"question", "answer", "confirm"})

# What the assistant said unprompted: reactive advisories and proactive heads-ups.
VOLUNTEERED_KINDS = frozenset({"advisory", "heads_up"})


def select_context_turns(
    turns: list[ChatTurn], *, dialogue_max: int, volunteered_max: int
) -> list[ChatTurn]:
    """The two-window rule: the last ``dialogue_max`` dialogue turns AND the last
    ``volunteered_max`` volunteered turns, merged chronologically.

    A single "last N" window would be wrong here: advisories fire per beacon command,
    so the most recent N entries can be entirely advisories, evicting the operator's
    question — the very thing the history exists to carry. Each group therefore gets a
    guaranteed quota.

    Pure and non-mutating. ``turns`` is expected in file order (oldest first). A turn
    whose ``kind`` is in neither group is ignored, so a future kind cannot silently
    leak into the model's context. A cap of 0 selects nothing from that group. The
    sort is stable, so turns sharing a ``ts`` keep a deterministic order.
    """
    window: list[ChatTurn] = []
    if dialogue_max > 0:
        window += [t for t in turns if t.kind in DIALOGUE_KINDS][-dialogue_max:]
    if volunteered_max > 0:
        window += [t for t in turns if t.kind in VOLUNTEERED_KINDS][-volunteered_max:]
    return sorted(window, key=lambda t: t.ts)


__all__ = ["DIALOGUE_KINDS", "VOLUNTEERED_KINDS", "ChatTurn", "select_context_turns"]
