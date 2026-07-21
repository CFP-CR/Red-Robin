"""Operator-correction lessons (spec §6.D) — the learning loop's application side.

``LessonStore`` is the narrow Protocol the assistant depends on (``LongtermRepo``
duck-satisfies it), so the assistant stays free of a direct infra import
(mirrors the ``AgentLLM`` Protocol). ``capture_lesson`` (Task 5.4) turns a parsed
correction into a stored ``Lesson`` with system-attached provenance."""

from __future__ import annotations

from typing import Protocol

from redrobin.domain.memory import Lesson


class LessonStore(Protocol):
    def add_operator_lesson(self, lesson: Lesson) -> Lesson: ...

    def list_operator_lessons(self, *, engagement_id: str | None) -> list[Lesson]: ...


__all__ = ["LessonStore"]
