"""Interpretation producer + confirm resolution (spec §6.C). Pure helpers — the
Assistant wires them into the bus. The assistant NEVER writes WM; it emits
engagement.milestone events the reducer applies (single-writer)."""

from __future__ import annotations

import re
from typing import Any

from redrobin.domain.assistant import Interpretation
from redrobin.domain.memory import PendingMilestone

# Word-set matching (NOT substring — "y" in "yet" was a reject-becomes-commit bug).
# Negation is checked FIRST so the safe default is never-commit.
_AFFIRMATIVE = {"yes", "y", "yeah", "yep", "yup", "confirm", "ok", "okay", "approved", "sí", "si"}
_NEGATIVE = {"no", "n", "nope", "not", "cancel", "reject", "stop", "wait", "later", "deny", "dont"}


def _milestone_payload(it: Interpretation, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": it.phase,
        "milestone_id": it.milestone_id,
        "status": it.status,
        "progress_pct": it.progress_pct,
    }
    payload.update(extra)
    return payload


def milestone_events(it: Interpretation) -> list[dict[str, Any]]:
    """The bus events for an interpretation. Soft (needs_confirm=False) → one commit
    milestone. Close/commit → a pending milestone (persisted for continuity) + an
    operator.question (the binary ask; context_refs mark it as OUR proposal)."""
    if not it.needs_confirm:
        return [{"type": "engagement.milestone", "payload": _milestone_payload(it)}]
    return [
        {
            "type": "engagement.milestone",
            "payload": _milestone_payload(it, pending=True, prompt=it.prompt),
        },
        {
            "type": "operator.question",
            "payload": {"text": it.prompt, "context_refs": [f"milestone:{it.milestone_id}"]},
        },
    ]


def resolve_confirm(
    *, answer_payload: dict[str, Any], pending_milestones: list[PendingMilestone]
) -> dict[str, Any] | None:
    """Resolve an operator.answer against the persisted pending proposals. Returns the
    engagement.milestone to publish (commit if clearly affirmative, drop if clearly
    negative), or None if it matches no pending milestone OR the answer is ambiguous
    (leave the pending alive — never guess a close/commit). An empty question_id (a
    plain operator ask carries none) never matches — no loop, no empty-id collision."""
    qid = str(answer_payload.get("question_id") or "")
    if not qid:
        return None
    match = next((p for p in pending_milestones if p.milestone_id == qid), None)
    if match is None:
        return None
    words = set(re.findall(r"[a-zà-ÿ']+", str(answer_payload.get("text") or "").lower()))
    if words & _NEGATIVE:  # negation FIRST — the safe default is never-commit
        return {
            "type": "engagement.milestone",
            "payload": {
                "milestone_id": match.milestone_id,
                "phase": "",
                "status": "",
                "progress_pct": 0,
                "drop": True,
            },
        }
    if words & _AFFIRMATIVE:
        return {
            "type": "engagement.milestone",
            "payload": {
                "phase": match.phase,
                "milestone_id": match.milestone_id,
                "status": match.status,
                "progress_pct": match.progress_pct,
            },
        }
    return None  # ambiguous — leave the pending alive, don't commit on a guess


def is_own_proposal_question(question_payload: dict[str, Any]) -> bool:
    """True if an operator.question is the assistant's OWN confirm proposal (so the
    conversational handler skips it — it is not an operator ask)."""
    refs = question_payload.get("context_refs")
    return isinstance(refs, list) and any(str(r).startswith("milestone:") for r in refs)


__all__ = ["milestone_events", "resolve_confirm", "is_own_proposal_question"]
