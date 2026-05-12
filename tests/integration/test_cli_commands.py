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


@pytest.mark.asyncio
async def test_replay_posts_each_line(tmp_path):
    import json

    import httpx

    from redrobin.cli_helpers.replay import replay_to_daemon

    src = tmp_path / "trace.jsonl"
    src.write_text(
        json.dumps({"type": "beacon.command_output", "payload": {"bid": "B-1", "output": "hi"}})
        + "\n"
        + json.dumps(
            {"type": "beacon.initial", "payload": {"bid": "B-2", "host": "WIN10"}}
        )
        + "\n"
    )

    posted: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content))
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        n = await replay_to_daemon(
            file=src, client=client, base_url="http://daemon", token=None
        )
    assert n == 2
    assert posted[0]["type"] == "beacon.command_output"
    assert posted[1]["type"] == "beacon.initial"


@pytest.mark.asyncio
async def test_replay_skips_blank_lines(tmp_path):
    import json

    import httpx

    from redrobin.cli_helpers.replay import replay_to_daemon

    src = tmp_path / "trace.jsonl"
    src.write_text(
        "\n"
        + json.dumps({"type": "beacon.command_output", "payload": {"bid": "B-1"}})
        + "\n"
        + "   \n"
        + json.dumps({"type": "beacon.initial", "payload": {"bid": "B-2"}})
        + "\n"
    )

    posted: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content))
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        n = await replay_to_daemon(
            file=src, client=client, base_url="http://daemon", token=None
        )
    assert n == 2
    assert len(posted) == 2


@pytest.mark.asyncio
async def test_replay_sends_bearer_token_when_provided(tmp_path):
    import json

    import httpx

    from redrobin.cli_helpers.replay import replay_to_daemon

    src = tmp_path / "trace.jsonl"
    src.write_text(json.dumps({"type": "beacon.command_output"}) + "\n")

    seen_auth: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_auth.append(req.headers.get("authorization"))
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        n = await replay_to_daemon(
            file=src, client=client, base_url="http://daemon", token="secret-xyz"
        )
    assert n == 1
    assert seen_auth == ["Bearer secret-xyz"]


def test_replay_endpoint_publishes_to_event_bus(tmp_path, monkeypatch):
    """POST /v1/events/replay forwards the body to the in-process EventBus."""
    import asyncio

    from fastapi.testclient import TestClient

    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(root / "rules"))
    # Slice 3: opt into degraded Layer B explicitly. No API keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()

    from redrobin.api.app import create_app
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app = create_app()
    auth = {"Authorization": f"Bearer {get_or_create_token()}"}
    payload = {"type": "beacon.command_output", "payload": {"bid": "B-1", "output": "hi"}}

    seen: list[dict] = []
    with TestClient(app) as client:
        # Subscribe directly to the daemon's bus to capture published events.
        bus = app.state.event_bus
        q = asyncio.get_event_loop().run_until_complete(bus.subscribe())

        r = client.post("/v1/events/replay", json=payload, headers=auth)
        assert r.status_code == 202
        assert r.json() == {"status": "accepted"}

        # Drain the queue (publish is sync at the queue level via put_nowait).
        async def _drain():
            while not q.empty():
                seen.append(await q.get())

        asyncio.get_event_loop().run_until_complete(_drain())

    types = [e.get("type") for e in seen]
    assert "beacon.command_output" in types


def test_replay_endpoint_requires_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(root / "rules"))
    # Slice 3: opt into degraded Layer B explicitly. No API keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()

    from redrobin.api.app import create_app

    with TestClient(create_app()) as client:
        r = client.post("/v1/events/replay", json={"type": "beacon.command_output"})
    assert r.status_code in (401, 422)
