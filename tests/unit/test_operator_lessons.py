# tests/unit/test_operator_lessons.py
from __future__ import annotations

from datetime import datetime

from redrobin.domain.memory import Lesson
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def _lesson(scope: str, engagement_id: str | None, lesson: str = "prefer comsvcs") -> Lesson:
    return Lesson(
        scope=scope,
        context="EDR observed (CrowdStrike)",
        lesson=lesson,
        why="operator flagged mimikatz too loud",
        engagement_id=engagement_id,
        created_at=datetime(2026, 7, 20, 12, 0, 0),
    )


def test_add_and_read_back_operator_scoped_persists_across_engagements(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", "eng-A"))
    # a DIFFERENT engagement still sees the operator-scoped lesson
    got = repo.list_operator_lessons(engagement_id="eng-B")
    assert len(got) == 1
    assert got[0].scope == "operator"
    assert got[0].lesson == "prefer comsvcs"
    assert got[0].id is not None


def test_engagement_scoped_is_quarantined_to_its_own_engagement(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("engagement", "eng-A", lesson="A-only"))
    # its OWN engagement sees it
    assert [x.lesson for x in repo.list_operator_lessons(engagement_id="eng-A")] == ["A-only"]
    # a different engagement does NOT (dies with the op)
    assert repo.list_operator_lessons(engagement_id="eng-B") == []


def test_read_back_mixes_operator_and_own_engagement(tmp_path):
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", "eng-A", lesson="op-wide"))
    repo.add_operator_lesson(_lesson("engagement", "eng-A", lesson="A-only"))
    repo.add_operator_lesson(_lesson("engagement", "eng-B", lesson="B-only"))
    got = {x.lesson for x in repo.list_operator_lessons(engagement_id="eng-A")}
    assert got == {"op-wide", "A-only"}  # NOT B-only


def test_operator_lessons_table_is_separate_from_promoted_lessons(tmp_path):
    # adding an operator lesson does not disturb the PromotedLesson API
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(_lesson("operator", None))
    assert repo.list_lessons() == []  # PromotedLesson table still empty
