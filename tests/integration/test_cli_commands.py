"""Slice 11 integration: daemon stop, kb add, replay."""
from __future__ import annotations

import os

import pytest

from redrobin.cli_helpers.daemon_pidfile import (
    PidFile,
    is_pid_alive,
    read_pid,
    write_pid,
)


def test_pidfile_roundtrip(tmp_path):
    p = tmp_path / "rr.pid"
    write_pid(p, 12345)
    assert read_pid(p) == 12345
    p.unlink()


def test_is_pid_alive_for_current_process():
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_for_dead_pid():
    assert is_pid_alive(0) is False


def test_pidfile_context(tmp_path):
    p = tmp_path / "rr.pid"
    with PidFile(p) as pf:
        assert read_pid(p) == os.getpid()
        assert pf.path == p
    assert not p.exists()


@pytest.mark.asyncio
async def test_kb_ingest_chunks_and_embeds(tmp_path):
    from redrobin.cli_helpers.kb_ingest import ingest_file

    src = tmp_path / "blog.md"
    src.write_text("# Mimikatz Detection\n" + ("Paragraph about LSASS access. " * 50))

    embedded: list[list[str]] = []

    class FakeEmbedder:
        async def embed_documents(self, texts):
            embedded.append(texts)
            return [[0.1, 0.2, 0.3] for _ in texts]

    stored: list[dict] = []

    class FakeStore:
        async def upsert(self, *, doc_id, chunk_id, text, vector, metadata):
            stored.append(
                {
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                    "metadata": metadata,
                    "text": text,
                }
            )

    count = await ingest_file(
        path=src,
        tags=["mimikatz"],
        embedder=FakeEmbedder(),
        store=FakeStore(),
        chunk_size=200,
        chunk_overlap=20,
    )
    assert count > 0
    assert len(stored) == count
    assert all("mimikatz" in s["metadata"]["tags"] for s in stored)


@pytest.mark.asyncio
async def test_kb_ingest_uses_sha256_doc_id(tmp_path):
    """doc_id must be deterministic across calls for the same file path."""
    import hashlib

    from redrobin.cli_helpers.kb_ingest import ingest_file

    src = tmp_path / "blog.md"
    src.write_text("short content")

    class FakeEmbedder:
        async def embed_documents(self, texts):
            return [[0.1] for _ in texts]

    seen_doc_ids: set[str] = set()

    class FakeStore:
        async def upsert(self, *, doc_id, chunk_id, text, vector, metadata):
            seen_doc_ids.add(doc_id)

    await ingest_file(
        path=src,
        tags=[],
        embedder=FakeEmbedder(),
        store=FakeStore(),
    )
    expected = hashlib.sha256(str(src.resolve()).encode()).hexdigest()[:16]
    assert seen_doc_ids == {expected}


@pytest.mark.asyncio
async def test_kb_ingest_chunk_ids_are_indexed(tmp_path):
    """chunk_id must encode the chunk's position so upsert can dedupe."""
    from redrobin.cli_helpers.kb_ingest import ingest_file

    src = tmp_path / "blog.md"
    src.write_text("A" * 1000)

    class FakeEmbedder:
        async def embed_documents(self, texts):
            return [[0.1] for _ in texts]

    chunk_ids: list[str] = []

    class FakeStore:
        async def upsert(self, *, doc_id, chunk_id, text, vector, metadata):
            chunk_ids.append(chunk_id)

    await ingest_file(
        path=src,
        tags=[],
        embedder=FakeEmbedder(),
        store=FakeStore(),
        chunk_size=200,
        chunk_overlap=20,
    )
    # IDs should be unique and ordered
    assert len(chunk_ids) == len(set(chunk_ids))
    assert chunk_ids == sorted(chunk_ids, key=lambda s: int(s.rsplit("_", 1)[-1]))
