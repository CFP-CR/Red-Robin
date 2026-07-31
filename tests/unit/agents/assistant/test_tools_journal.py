from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from redrobin.application.agents.assistant.tools import build_journal_service, build_tool_registry
from redrobin.application.journal_service import JournalService
from redrobin.infrastructure.vector_store import VectorStore


def _seeded_journal() -> JournalService:
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    store = VectorStore()
    store.add(
        ids=["journal:c1#0"],
        texts=["$ mimikatz sekurlsa::logonpasswords\nsvc_sql:hash:aad3b..."],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "journal:c1"}],
    )
    # events_path points nowhere -> retrieve()'s self-refresh is a no-op, so the
    # test exercises retrieval over the pre-seeded store deterministically.
    return JournalService(voyage=voyage, store=store, events_path=Path("does-not-exist.jsonl"))


def test_registry_exposes_kb_and_journal_tools():
    tools = build_tool_registry(None, _seeded_journal())
    assert [t.name for t in tools] == ["kb_retrieve", "search_journal", "get_lessons"]
    assert tools[1].input_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_search_journal_returns_seeded_moment():
    tools = build_tool_registry(None, _seeded_journal())
    out = await tools[1].handler({"query": "what creds did we dump"})
    assert "journal:c1" in out
    assert "svc_sql" in out


@pytest.mark.asyncio
async def test_search_journal_degraded_without_journal_is_graceful():
    tools = build_tool_registry(None, None)  # journal defaults None
    out = await tools[1].handler({"query": "anything"})
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_search_journal_empty_query_is_handled():
    tools = build_tool_registry(None, _seeded_journal())
    out = await tools[1].handler({})
    assert "no query" in out.lower()


@pytest.mark.asyncio
async def test_search_journal_no_hits_reports_not_dumps():
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    empty = JournalService(
        voyage=voyage, store=VectorStore(), events_path=Path("does-not-exist.jsonl")
    )
    tools = build_tool_registry(None, empty)
    out = await tools[1].handler({"query": "nothing indexed"})
    assert out == "No matching journal entries found."


@pytest.mark.asyncio
async def test_search_journal_retrieval_failure_is_graceful():
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(side_effect=RuntimeError("voyage unreachable"))
    store = VectorStore()
    store.add(
        ids=["journal:c1#0"],
        texts=["x"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "journal:c1"}],
    )
    journal = JournalService(voyage=voyage, store=store, events_path=Path("does-not-exist.jsonl"))
    tools = build_tool_registry(None, journal)
    out = await tools[1].handler({"query": "anything"})
    assert out == "Operational journal retrieval failed."


@pytest.mark.asyncio
async def test_build_journal_service_none_without_active_engagement(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    from redrobin.application.engagement_service import EngagementService

    monkeypatch.setattr(EngagementService, "current", lambda self: None)
    assert await build_journal_service() is None


@pytest.mark.asyncio
async def test_build_journal_service_degraded_without_voyage_key(monkeypatch, tmp_path):
    # Force an active engagement so the factory reaches VoyageClient() (not the
    # no-engagement early return); with VOYAGE_API_KEY unset, VoyageClient() raises
    # RuntimeError -> degrades to None. Hermetic (REDROBIN_ROOT=tmp_path).
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from redrobin.application.engagement_service import EngagementService

    monkeypatch.setattr(EngagementService, "current", lambda self: "eng-x")
    assert await build_journal_service() is None
