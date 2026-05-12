"""Persistence unit tests — sync sqlite3 API for EpisodicRepo + LongtermRepo.

The original async SQLAlchemy API was removed in Slice 9 of the v0.1.x
hardening pass (single-mode repos). Tests below exercise the surviving
sync surface: ``EpisodicRepo.record`` / ``list_episodes_for_engagement``
and ``LongtermRepo.upsert_lesson`` / ``list_lessons``.
"""

from __future__ import annotations

from datetime import datetime

from redrobin.domain.memory import PromotedLesson
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def test_episodic_record_and_list(tmp_path) -> None:
    repo = EpisodicRepo(tmp_path / "episodic.db")
    episode = repo.record(
        engagement_id="acme-q2",
        correlation_id="corr-1",
        command="whoami",
        verdict="BLOCK",
        reason="opsec policy",
    )
    assert episode.id is not None
    assert episode.engagement_id == "acme-q2"
    assert episode.verdict == "BLOCK"

    episodes = repo.list_episodes_for_engagement("acme-q2")
    assert len(episodes) == 1
    assert episodes[0].command == "whoami"


def test_episodic_isolates_per_engagement(tmp_path) -> None:
    repo = EpisodicRepo(tmp_path / "episodic.db")
    repo.record(
        engagement_id="acme-q2",
        correlation_id="c1",
        command="nanodump",
        verdict="WARN",
    )
    repo.record(
        engagement_id="globex-q3",
        correlation_id="c2",
        command="rubeus",
        verdict="BLOCK",
    )

    acme = repo.list_episodes_for_engagement("acme-q2")
    globex = repo.list_episodes_for_engagement("globex-q3")
    assert {ep.command for ep in acme} == {"nanodump"}
    assert {ep.command for ep in globex} == {"rubeus"}


def test_longterm_upsert_and_list(tmp_path) -> None:
    repo = LongtermRepo(tmp_path / "longterm.db")
    lesson = repo.upsert_lesson(
        PromotedLesson(
            id=None,
            command="nanodump.bof",
            verdict="BLOCK",
            summary="nanodump blocked by Falcon",
            occurrence_count=3,
            created_at=datetime.now(),
        )
    )
    assert lesson.id is not None

    all_lessons = repo.list_lessons()
    assert len(all_lessons) == 1
    assert all_lessons[0].command == "nanodump.bof"
    assert all_lessons[0].occurrence_count == 3
