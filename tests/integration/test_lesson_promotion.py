"""Slice 10 integration: verdict -> episode -> lesson promotion.

The reducer records every ``opsec.verdict`` event into the ``EpisodicRepo`` as
a raw episode. When the same ``(command, verdict)`` pair recurs at or above
the configured threshold with verdict in ``{BLOCK, WARN}``, the
``LessonPromoter`` synthesises a single promoted-lesson record into the
``LongtermRepo`` so future LLM judgments can retrieve it from L1.

Phase 1's async-SQL ``EpisodicRepo``/``LongtermRepo`` keep their original
``Lesson``/``Fact`` shape; this slice adds a parallel **sync, file-backed**
``record()`` / ``upsert_lesson()`` API constructed from a ``Path`` so it
matches the synchronous reducer dispatch path (no event loop hop per event).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.lesson_promoter import LessonPromoter
from redrobin.application.working_memory_reducer import WorkingMemoryReducer
from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _seeded_wm_repo(tmp_path: Path, engagement_id: str) -> WorkingMemoryRepo:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id=engagement_id))
    return repo


@pytest.mark.asyncio
async def test_verdict_records_episode(tmp_path: Path) -> None:
    """Every opsec.verdict event is appended to EpisodicRepo as an Episode."""
    bus = EventBus()
    wm_repo = _seeded_wm_repo(tmp_path, "eng-test")
    ep_repo = EpisodicRepo(tmp_path / "episodic.db")
    reducer = WorkingMemoryReducer(
        bus=bus,
        repo=wm_repo,
        engagement_id="eng-test",
        episodic_repo=ep_repo,
    )
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(reducer.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "c-1",
                    "command": "mimikatz",
                    "verdict": "BLOCK",
                    "reason": "high detection",
                },
            }
        )
        # Allow processing — drive the loop until the episode lands.
        episodes: list = []
        for _ in range(50):
            await asyncio.sleep(0.02)
            episodes = ep_repo.list_episodes_for_engagement("eng-test")
            if episodes:
                break
        assert any(
            e.command == "mimikatz" and e.verdict == "BLOCK" for e in episodes
        )
    finally:
        await bus.close()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_reducer_records_non_block_verdicts_too(tmp_path: Path) -> None:
    """Episode log captures full operator activity, not only BLOCK."""
    bus = EventBus()
    wm_repo = _seeded_wm_repo(tmp_path, "eng-full")
    ep_repo = EpisodicRepo(tmp_path / "episodic.db")
    reducer = WorkingMemoryReducer(
        bus=bus, repo=wm_repo, engagement_id="eng-full", episodic_repo=ep_repo
    )
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(reducer.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        for verdict in ("ALLOW", "WARN", "BLOCK"):
            await bus.publish(
                {
                    "type": "opsec.verdict",
                    "payload": {
                        "correlation_id": f"c-{verdict}",
                        "command": "ls",
                        "verdict": verdict,
                        "reason": "ok",
                    },
                }
            )
        episodes: list = []
        for _ in range(50):
            await asyncio.sleep(0.02)
            episodes = ep_repo.list_episodes_for_engagement("eng-full")
            if len(episodes) >= 3:
                break
        verdicts = {e.verdict for e in episodes}
        assert {"ALLOW", "WARN", "BLOCK"}.issubset(verdicts)
        # Existing BLOCK-only finding still emitted — episode log is additive.
        findings = wm_repo.load().findings
        assert sum(1 for f in findings if f.kind == "opsec_block") == 1
    finally:
        await bus.close()
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_promoter_creates_lesson_on_repeated_pattern(tmp_path: Path) -> None:
    """Three or more BLOCK episodes for the same command produce one Lesson."""
    ep_repo = EpisodicRepo(tmp_path / "ep.db")
    lt_repo = LongtermRepo(tmp_path / "lt.db")
    promoter = LessonPromoter(
        episodic_repo=ep_repo, longterm_repo=lt_repo, min_occurrences=3
    )
    for i in range(3):
        ep_repo.record(
            engagement_id="eng-test",
            correlation_id=f"c-{i}",
            command="mimikatz",
            verdict="BLOCK",
            reason="high detection",
        )

    promoted = await promoter.promote("eng-test")

    assert promoted == 1
    lessons = lt_repo.list_lessons()
    assert any("mimikatz" in lesson.summary for lesson in lessons)
    assert all(lesson.occurrence_count >= 3 for lesson in lessons)


@pytest.mark.asyncio
async def test_promoter_skips_allow_and_below_threshold(tmp_path: Path) -> None:
    ep_repo = EpisodicRepo(tmp_path / "ep.db")
    lt_repo = LongtermRepo(tmp_path / "lt.db")
    promoter = LessonPromoter(
        episodic_repo=ep_repo, longterm_repo=lt_repo, min_occurrences=3
    )
    # 5x ALLOW for "whoami" — should not promote (verdict gate).
    for i in range(5):
        ep_repo.record(
            engagement_id="eng-t",
            correlation_id=f"a-{i}",
            command="whoami",
            verdict="ALLOW",
            reason="benign",
        )
    # 2x BLOCK for "rubeus" — below threshold.
    for i in range(2):
        ep_repo.record(
            engagement_id="eng-t",
            correlation_id=f"b-{i}",
            command="rubeus",
            verdict="BLOCK",
            reason="not yet",
        )

    promoted = await promoter.promote("eng-t")
    assert promoted == 0
    assert lt_repo.list_lessons() == []


@pytest.mark.asyncio
async def test_promoter_is_idempotent(tmp_path: Path) -> None:
    ep_repo = EpisodicRepo(tmp_path / "ep.db")
    lt_repo = LongtermRepo(tmp_path / "lt.db")
    promoter = LessonPromoter(
        episodic_repo=ep_repo, longterm_repo=lt_repo, min_occurrences=3
    )
    for i in range(3):
        ep_repo.record(
            engagement_id="eng-x",
            correlation_id=f"c-{i}",
            command="mimikatz",
            verdict="BLOCK",
            reason="high detection",
        )

    first = await promoter.promote("eng-x")
    second = await promoter.promote("eng-x")

    assert first == 1
    assert second == 0
    assert len(lt_repo.list_lessons()) == 1


@pytest.mark.asyncio
async def test_promoter_updates_occurrence_count_when_pattern_grows(
    tmp_path: Path,
) -> None:
    ep_repo = EpisodicRepo(tmp_path / "ep.db")
    lt_repo = LongtermRepo(tmp_path / "lt.db")
    promoter = LessonPromoter(
        episodic_repo=ep_repo, longterm_repo=lt_repo, min_occurrences=3
    )
    for i in range(3):
        ep_repo.record(
            engagement_id="eng-g",
            correlation_id=f"c-{i}",
            command="mimikatz",
            verdict="BLOCK",
            reason="r1",
        )
    await promoter.promote("eng-g")

    # Two more occurrences arrive.
    for i in range(3, 5):
        ep_repo.record(
            engagement_id="eng-g",
            correlation_id=f"c-{i}",
            command="mimikatz",
            verdict="BLOCK",
            reason="r2",
        )
    promoted = await promoter.promote("eng-g")
    assert promoted == 0  # idempotent: no new row inserted
    lessons = lt_repo.list_lessons()
    assert len(lessons) == 1
    assert lessons[0].occurrence_count == 5


@pytest.mark.asyncio
async def test_lifespan_runs_promoter_periodically(tmp_path: Path, monkeypatch) -> None:
    # Isolate from any active engagement on the dev machine's real ~/.redrobin/
    # so EngagementService.current() returns None and the env-var-set
    # REDROBIN_ENGAGEMENT_ID below wins via the degraded fallback path.
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_PROMOTER_INTERVAL_SEC", "0.1")
    monkeypatch.setenv("REDROBIN_PROMOTER_MIN_OCCURRENCES", "2")
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-prom")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        ep_repo = app.state.episodic_repo
        for i in range(2):
            ep_repo.record(
                engagement_id="eng-prom",
                correlation_id=f"c-{i}",
                command="mimikatz",
                verdict="BLOCK",
                reason="high detection",
            )
        # One interval tick + commit lag — generous bound for slow CI.
        for _ in range(40):
            await asyncio.sleep(0.05)
            lessons = app.state.longterm_repo.list_lessons()
            if lessons:
                break
        lessons = app.state.longterm_repo.list_lessons()
        assert any("mimikatz" in lesson.summary for lesson in lessons)
