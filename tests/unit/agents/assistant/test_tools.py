from unittest.mock import AsyncMock

import pytest

from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.application.kb_service import KBService
from redrobin.infrastructure.vector_store import VectorStore


def _seeded_kb() -> KBService:
    store = VectorStore()
    store.add(
        ids=["chunk0"],
        texts=["comsvcs.dll MiniDump is a quieter lsass dump than mimikatz"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "vault/Techniques/lsass.md"}],
    )
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    return KBService(voyage=voyage, store=store)


def test_registry_exposes_kb_retrieve():
    tools = build_tool_registry(_seeded_kb())
    assert [t.name for t in tools] == ["kb_retrieve"]
    assert tools[0].input_schema["required"] == ["query"]
    assert tools[0].input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_kb_retrieve_returns_seeded_source_and_excerpt():
    tools = build_tool_registry(_seeded_kb())
    out = await tools[0].handler({"query": "quiet lsass dump"})
    assert "vault/Techniques/lsass.md" in out
    assert "comsvcs.dll" in out


@pytest.mark.asyncio
async def test_kb_retrieve_degraded_without_kb_is_graceful():
    tools = build_tool_registry(None)
    out = await tools[0].handler({"query": "anything"})
    assert "unavailable" in out.lower()


@pytest.mark.asyncio
async def test_kb_retrieve_empty_query_is_handled():
    tools = build_tool_registry(_seeded_kb())
    out = await tools[0].handler({})
    assert "no query" in out.lower()
