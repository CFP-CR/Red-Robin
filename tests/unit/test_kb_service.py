"""KB service — index + retrieve with mocked Voyage embeddings."""

from unittest.mock import AsyncMock

import pytest

from redrobin.application.kb_service import KBService
from redrobin.infrastructure.kb_indexer import chunk_markdown, walk_markdown_files
from redrobin.infrastructure.vector_store import VectorStore


def test_chunk_markdown_small_text_returns_single_chunk():
    chunks = chunk_markdown("short text")
    assert chunks == ["short text"]


def test_chunk_markdown_long_text_splits_with_overlap():
    text = "A" * 2000
    chunks = chunk_markdown(text, size=800, overlap=100)
    assert len(chunks) == 3  # 0-800, 700-1500, 1400-2000
    assert chunks[1].startswith("A" * 100)  # overlap


def test_walk_markdown_finds_md_files(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.markdown").write_text("b")
    (tmp_path / "c.txt").write_text("c")  # skip
    files = walk_markdown_files(tmp_path)
    assert len(files) == 2
    assert {p.name for p in files} == {"a.md", "b.markdown"}


def test_vector_store_search_returns_top_k_by_cosine():
    store = VectorStore()
    store.add(
        ids=["a", "b", "c"],
        texts=["alpha", "beta", "gamma"],
        vectors=[[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
    )
    hits = store.search(query_vector=[1.0, 0.0], top_k=2)
    assert len(hits) == 2
    # "a" is identical to query → highest sim
    assert hits[0][0].id == "a"


def test_vector_store_search_empty_returns_empty():
    store = VectorStore()
    assert store.search(query_vector=[1.0, 0.0]) == []


def test_vector_store_save_load_roundtrip(tmp_path):
    store = VectorStore()
    store.add(
        ids=["x"],
        texts=["hello"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "test.md"}],
    )
    path = tmp_path / "index.npz"
    store.save(path)
    loaded = VectorStore.load(path)
    assert loaded.count() == 1
    hits = loaded.search(query_vector=[1.0, 0.0], top_k=1)
    assert hits[0][0].id == "x"
    assert hits[0][0].metadata == {"source": "test.md"}


@pytest.mark.asyncio
async def test_kb_service_index_directory(tmp_path):
    (tmp_path / "doc.md").write_text("This is a doc about mimikatz.")
    voyage = AsyncMock()
    voyage.embed_documents = AsyncMock(
        return_value=type("R", (), {"vectors": [[1.0, 0.0]], "total_tokens": 5})()
    )
    service = KBService(voyage=voyage, store=VectorStore())
    count = await service.index_directory(tmp_path)
    assert count == 1
    voyage.embed_documents.assert_awaited_once()


@pytest.mark.asyncio
async def test_kb_service_retrieve_empty_store_returns_empty(tmp_path):
    voyage = AsyncMock()
    service = KBService(voyage=voyage, store=VectorStore())
    result = await service.retrieve("any query")
    assert result == []


@pytest.mark.asyncio
async def test_kb_service_retrieve_returns_top_k_with_source():
    store = VectorStore()
    store.add(
        ids=["chunk0"],
        texts=["mimikatz dumps lsass"],
        vectors=[[1.0, 0.0]],
        metadatas=[{"source": "vault/mimikatz.md"}],
    )
    voyage = AsyncMock()
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    service = KBService(voyage=voyage, store=store)
    hits = await service.retrieve("how does mimikatz work?", top_k=1)
    assert len(hits) == 1
    text, source, score = hits[0]
    assert text == "mimikatz dumps lsass"
    assert source == "vault/mimikatz.md"
    assert score > 0.99  # near-perfect match
