from __future__ import annotations

from datetime import datetime

import pytest

from redrobin.application.agents.assistant.lessons import capture_lesson
from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.domain.memory import Lesson
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def _store(tmp_path) -> LongtermRepo:
    repo = LongtermRepo(tmp_path / "longterm.db")
    repo.add_operator_lesson(
        Lesson(
            scope="operator",
            context="EDR observed",
            lesson="prefer comsvcs",
            why="mimikatz too loud",
            engagement_id="eng-A",
            created_at=datetime(2026, 7, 20),
        )
    )
    repo.add_operator_lesson(
        Lesson(
            scope="engagement",
            context="honeypot suspected",
            lesson="avoid DC01",
            why="looks like a decoy",
            engagement_id="eng-OTHER",
            created_at=datetime(2026, 7, 20),
        )
    )
    return repo


def test_registry_appends_get_lessons(tmp_path):
    tools = build_tool_registry(
        None, None, lesson_store=_store(tmp_path), engagement_provider=lambda: "eng-A"
    )
    assert [t.name for t in tools] == ["kb_retrieve", "search_journal", "get_lessons"]


@pytest.mark.asyncio
async def test_get_lessons_returns_operator_plus_own_engagement(tmp_path):
    tools = build_tool_registry(
        None, None, lesson_store=_store(tmp_path), engagement_provider=lambda: "eng-A"
    )
    out = await tools[2].handler({})
    assert "prefer comsvcs" in out  # operator-scoped
    assert "avoid DC01" not in out  # engagement-scoped for a DIFFERENT engagement — quarantined


@pytest.mark.asyncio
async def test_get_lessons_degraded_without_store_is_graceful(tmp_path):
    tools = build_tool_registry(None, None, lesson_store=None)
    out = await tools[2].handler({})
    assert "no operator lessons" in out.lower() or "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_get_lessons_empty_is_graceful(tmp_path):
    empty = LongtermRepo(tmp_path / "longterm.db")
    tools = build_tool_registry(None, None, lesson_store=empty, engagement_provider=lambda: "eng-A")
    out = await tools[2].handler({})
    assert "no operator lessons" in out.lower()


def test_capture_lesson_system_attaches_provenance(tmp_path):
    store = LongtermRepo(tmp_path / "longterm.db")
    parsed = {"scope": "operator", "context": "EDR", "lesson": "prefer comsvcs", "why": "too loud"}
    le = capture_lesson(parsed, engagement_id="eng-A", now=datetime(2026, 7, 20), store=store)
    assert le is not None and le.id is not None
    assert le.engagement_id == "eng-A"  # provenance system-attached, NOT from the model
    assert store.list_operator_lessons(engagement_id="eng-A")[0].lesson == "prefer comsvcs"


def test_capture_lesson_none_store_is_graceful():
    parsed = {"scope": "operator", "context": "x", "lesson": "y", "why": "z"}
    assert capture_lesson(parsed, engagement_id="e", now=datetime(2026, 7, 20), store=None) is None


@pytest.mark.asyncio
async def test_get_lessons_raising_provider_is_graceful(tmp_path):
    def _boom() -> str:
        raise RuntimeError("provider exploded")

    tools = build_tool_registry(
        None, None, lesson_store=_store(tmp_path), engagement_provider=_boom
    )
    out = await tools[2].handler({})
    assert out == "Operator-lesson retrieval failed."  # degraded, did NOT raise
