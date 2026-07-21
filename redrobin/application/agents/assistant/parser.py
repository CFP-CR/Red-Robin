"""Parse the agent's final message into a Recommendation.

Mirrors enrichment.parser.parse_deltas, but NEVER raises: a silent or malformed
cycle must not crash the daemon, so it returns None (the assistant simply does
not publish -> signal-not-noise). Enforces the thesis: a recommendation missing
the WHAT (``suggested_action``) or the WHY (``rationale``) is dropped.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from redrobin.domain.assistant import (
    OPERATOR_QUESTION_ID_PREFIX,
    Interpretation,
    Recommendation,
)
from redrobin.domain.events import Severity

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {s.value for s in Severity}


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_recommendation(raw: str, *, correlation_id: str | None) -> Recommendation | None:
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable recommendation JSON — staying silent")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("speak") is False:
        return None  # the model explicitly chose to stay silent
    _action = data.get("suggested_action")
    _rationale = data.get("rationale")
    action = _action.strip() if isinstance(_action, str) else ""
    rationale = _rationale.strip() if isinstance(_rationale, str) else ""
    if not action or not rationale:
        return None  # WHAT+WHY are mandatory; without them it is outsourcing, not advice
    risk_raw = str(data.get("opsec_risk", "INFO")).strip().upper()
    risk = Severity(risk_raw) if risk_raw in _VALID_SEVERITIES else Severity.INFO
    confidence = str(data.get("confidence") or "").strip()
    _refs = data.get("references")
    refs_raw = _refs if isinstance(_refs, list) else []
    references = [str(r) for r in refs_raw if isinstance(r, (str, int, float))]
    return Recommendation(
        suggested_action=action,
        rationale=rationale,
        opsec_risk=risk,
        confidence=confidence,
        references=references,
        correlation_id=correlation_id,
    )


@dataclass(frozen=True)
class ParsedAnswer:
    answer: str
    citations: list[str]
    lesson: dict[str, str] | None  # {scope, context, lesson, why} when the message was a correction


_LESSON_KEYS = ("scope", "context", "lesson", "why")


def _parse_lesson(raw_lesson: Any) -> dict[str, str] | None:
    """A correction's captured lesson, or None. Drops a malformed/partial lesson
    (never raises). scope must be a known value; all four fields must be non-empty."""
    if not isinstance(raw_lesson, dict):
        return None
    out: dict[str, str] = {}
    for k in _LESSON_KEYS:
        v = str(raw_lesson.get(k) or "").strip()
        if not v:
            return None
        out[k] = v
    if out["scope"] not in ("operator", "engagement"):
        return None
    return out


def parse_answer(raw: str) -> ParsedAnswer | None:
    """Parse the conversational cycle's output into an answer (+ optional captured
    lesson). NEVER raises — a malformed answer means the assistant simply does not
    reply (degraded-None). An answer with no text is dropped."""
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable answer JSON — staying silent")
        return None
    if not isinstance(data, dict):
        return None
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return None
    _cites = data.get("citations")
    cites_raw = _cites if isinstance(_cites, list) else []
    citations = [str(c) for c in cites_raw if isinstance(c, (str, int, float))]
    return ParsedAnswer(
        answer=answer, citations=citations, lesson=_parse_lesson(data.get("lesson"))
    )


def parse_interpretation(raw: str) -> Interpretation | None:
    """Parse the interpretation cycle's output, or None (no material change / silent).
    NEVER raises. Requires at least one of phase/milestone_id (else there is nothing
    to write). progress_pct clamped 0..100."""
    text = _strip_fences(raw)
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("assistant: unparseable interpretation JSON — no write")
        return None
    if not isinstance(data, dict):
        return None
    phase = str(data.get("phase") or "").strip()
    milestone_id = str(data.get("milestone_id") or "").strip()
    if milestone_id.startswith(OPERATOR_QUESTION_ID_PREFIX):
        milestone_id = ""  # reserved operator-question namespace — never a milestone (review #1)
    if not phase and not milestone_id:
        return None  # nothing to write
    try:
        progress = int(data.get("progress_pct") or 0)
    except (TypeError, ValueError):
        progress = 0
    progress = max(0, min(100, progress))
    # A confirm is always ABOUT a specific objective/kill-chain id — without one it
    # can't be tracked/resolved, so downgrade it to a soft auto-write (prevents an
    # untrackable empty-id pending that a plain operator answer could collide with).
    needs_confirm = bool(data.get("needs_confirm")) and bool(milestone_id)
    return Interpretation(
        phase=phase,
        milestone_id=milestone_id,
        status=str(data.get("status") or "").strip(),
        progress_pct=progress,
        needs_confirm=needs_confirm,
        prompt=str(data.get("prompt") or "").strip(),
    )


__all__ = ["ParsedAnswer", "parse_answer", "parse_interpretation", "parse_recommendation"]
