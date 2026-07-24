"""Operator-correction lessons (spec §6.D) — the learning loop's application side.

``LessonStore`` is the narrow Protocol the assistant depends on (``LongtermRepo``
duck-satisfies it), so the assistant stays free of a direct infra import
(mirrors the ``AgentLLM`` Protocol). ``capture_lesson`` (Task 5.4) turns a parsed
correction into a stored ``Lesson`` with system-attached provenance."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from redrobin.domain.memory import Lesson

logger = logging.getLogger(__name__)


class LessonStore(Protocol):
    def add_operator_lesson(self, lesson: Lesson) -> Lesson: ...

    def list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]: ...


def capture_lesson(
    parsed_lesson: dict[str, str],
    *,
    engagement_id: str | None,
    now: datetime,
    store: LessonStore | None,
) -> Lesson | None:
    """Turn a validated correction-lesson dict (from parse_answer) into a stored
    Lesson. Provenance (engagement_id, created_at) is SYSTEM-attached here, never
    model-supplied. Degraded/never-crash: no store or a store error -> None."""
    if store is None:
        return None
    try:
        return store.add_operator_lesson(
            Lesson(
                scope=parsed_lesson["scope"],
                context=parsed_lesson["context"],
                lesson=parsed_lesson["lesson"],
                why=parsed_lesson["why"],
                engagement_id=engagement_id,  # provenance, system-attached for BOTH scopes
                created_at=now,
            )
        )
    except Exception:
        logger.exception("assistant: capture_lesson failed")
        return None


__all__ = ["LessonStore", "capture_lesson"]
