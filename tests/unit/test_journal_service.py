from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import redrobin.application.journal_service as journal_module
from redrobin.application.journal_service import (
    JournalEntry,
    JournalService,
    _event_bodies,
    join_transcript,
)
from redrobin.infrastructure.vector_store import VectorStore


def test_join_pairs_input_and_output_by_correlation_id():
    bodies = [
        {
            "type": "beacon.command_input",
            "correlation_id": "c1",
            "raw": "whoami /all",
            "command": "whoami",
        },
        {"type": "beacon.command_output", "correlation_id": "c1", "output": "nt authority\\system"},
    ]
    assert join_transcript(bodies) == [
        JournalEntry(correlation_id="c1", command="whoami /all", output="nt authority\\system")
    ]


def test_join_prefers_raw_then_command():
    bodies = [
        {"type": "beacon.command_input", "correlation_id": "c2", "command": "ls"},  # no raw
        {"type": "beacon.command_output", "correlation_id": "c2", "output": "file.txt"},
    ]
    assert join_transcript(bodies)[0].command == "ls"


def test_join_skips_events_without_correlation_id():
    bodies = [
        {"type": "beacon.command_input", "raw": "no-corr"},
        {"type": "beacon.command_output", "output": "orphan"},
    ]
    assert join_transcript(bodies) == []


def test_join_output_without_matching_input_yields_empty_command():
    bodies = [{"type": "beacon.command_output", "correlation_id": "c3", "output": "stray output"}]
    assert join_transcript(bodies) == [
        JournalEntry(correlation_id="c3", command="", output="stray output")
    ]


def test_join_ignores_unrelated_event_types():
    bodies = [
        {"type": "beacon.initial", "correlation_id": "cx", "host": "WS01"},
        {"type": "beacon.command_input", "correlation_id": "c4", "raw": "ipconfig"},
        {"type": "beacon.command_output", "correlation_id": "c4", "output": "10.0.0.5"},
    ]
    assert [e.command for e in join_transcript(bodies)] == ["ipconfig"]


def test_join_persistent_commands_map_pairs_across_calls():
    # The input is in one batch, its output in the next — a shared `commands` map
    # lets the second call pair them (this is what the incremental refresh relies on).
    commands: dict[str, str] = {}
    first = join_transcript(
        [{"type": "beacon.command_input", "correlation_id": "c5", "raw": "netstat"}], commands
    )
    assert first == []  # input only, nothing to emit yet
    second = join_transcript(
        [{"type": "beacon.command_output", "correlation_id": "c5", "output": "LISTENING"}], commands
    )
    assert second == [JournalEntry(correlation_id="c5", command="netstat", output="LISTENING")]


def _envelope(event: dict) -> str:
    return json.dumps({"spec_version": "0.1", "id": "x", "ts": "t", "source": "cs", "event": event})


def _fixed_voyage() -> AsyncMock:
    """A fake Voyage whose embeddings match the batch size on every call."""
    voyage = AsyncMock()
    voyage.embed_documents = AsyncMock(
        side_effect=lambda texts: type(
            "R", (), {"vectors": [[1.0, 0.0]] * len(texts), "total_tokens": 0}
        )()
    )
    voyage.embed_query = AsyncMock(return_value=[1.0, 0.0])
    return voyage


def test_event_bodies_lifts_envelope_and_tolerates_garbage():
    lines = [
        _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "mimikatz"}),
        "   ",  # blank tolerated
        "{not valid json",  # malformed tolerated
        _envelope(
            {"type": "beacon.command_output", "correlation_id": "c1", "output": "svc_sql:hash"}
        ),
    ]
    bodies = _event_bodies(lines)
    assert [b.get("type") for b in bodies] == ["beacon.command_input", "beacon.command_output"]


@pytest.mark.asyncio
async def test_refresh_indexes_joined_entries(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope(
                    {
                        "type": "beacon.command_input",
                        "correlation_id": "c1",
                        "raw": "mimikatz sekurlsa::logonpasswords",
                    }
                ),
                _envelope(
                    {
                        "type": "beacon.command_output",
                        "correlation_id": "c1",
                        "output": "svc_sql:hash:aad3b...",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 1
    assert store.count() == 1


@pytest.mark.asyncio
async def test_refresh_is_incremental(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope(
                    {"type": "beacon.command_input", "correlation_id": "c1", "raw": "whoami"}
                ),
                _envelope(
                    {"type": "beacon.command_output", "correlation_id": "c1", "output": "system"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 1
    # append a NEW command; refresh picks up ONLY the new entry (not the whole file)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            _envelope({"type": "beacon.command_input", "correlation_id": "c2", "raw": "ipconfig"})
            + "\n"
        )
        f.write(
            _envelope(
                {"type": "beacon.command_output", "correlation_id": "c2", "output": "10.0.0.5"}
            )
            + "\n"
        )
    assert await svc.refresh() == 1
    assert store.count() == 2
    # nothing appended -> no-op
    assert await svc.refresh() == 0


@pytest.mark.asyncio
async def test_refresh_joins_output_to_input_from_an_earlier_chunk(tmp_path):
    # The input is written first, refreshed (remembered, nothing emitted), then the
    # output is appended and refreshed — the persistent commands map pairs them.
    path = tmp_path / "events.jsonl"
    path.write_text(
        _envelope({"type": "beacon.command_input", "correlation_id": "c9", "raw": "net group"})
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 0  # input only
    with path.open("a", encoding="utf-8") as f:
        f.write(
            _envelope(
                {"type": "beacon.command_output", "correlation_id": "c9", "output": "Domain Admins"}
            )
            + "\n"
        )
    assert await svc.refresh() == 1
    hits = await svc.retrieve("who is in domain admins", top_k=1)
    assert "net group" in hits[0][0] and "Domain Admins" in hits[0][0]


@pytest.mark.asyncio
async def test_retrieve_self_refreshes_then_returns_text_source_score(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope(
                    {"type": "beacon.command_input", "correlation_id": "c1", "raw": "mimikatz"}
                ),
                _envelope(
                    {
                        "type": "beacon.command_output",
                        "correlation_id": "c1",
                        "output": "svc_sql:hash",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    svc = JournalService(voyage=_fixed_voyage(), store=VectorStore(), events_path=path)
    # no explicit refresh() — retrieve() must catch the index up itself
    hits = await svc.retrieve("what credentials did we dump", top_k=1)
    assert len(hits) == 1
    text, source, _score = hits[0]
    assert "svc_sql" in text
    assert source == "journal:c1"


@pytest.mark.asyncio
async def test_refresh_missing_file_returns_zero(tmp_path):
    svc = JournalService(
        voyage=_fixed_voyage(), store=VectorStore(), events_path=tmp_path / "nope.jsonl"
    )
    assert await svc.refresh() == 0


@pytest.mark.asyncio
async def test_retrieve_on_empty_store_returns_empty(tmp_path):
    svc = JournalService(
        voyage=_fixed_voyage(), store=VectorStore(), events_path=tmp_path / "nope.jsonl"
    )
    assert await svc.retrieve("anything") == []


@pytest.mark.asyncio
async def test_refresh_multi_batch_indexes_all(tmp_path, monkeypatch):
    # Force multiple embed batches; every entry across batches is indexed.
    monkeypatch.setattr(journal_module, "_EMBED_BATCH", 2)
    path = tmp_path / "events.jsonl"
    lines = []
    for i in range(5):
        lines.append(
            _envelope({"type": "beacon.command_input", "correlation_id": f"k{i}", "raw": f"cmd{i}"})
        )
        lines.append(
            _envelope(
                {"type": "beacon.command_output", "correlation_id": f"k{i}", "output": f"out{i}"}
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 5
    assert store.count() == 5


@pytest.mark.asyncio
async def test_refresh_two_outputs_same_correlation_id_are_distinct_entries(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope({"type": "beacon.command_input", "correlation_id": "c1", "raw": "run"}),
                _envelope(
                    {"type": "beacon.command_output", "correlation_id": "c1", "output": "first"}
                ),
                _envelope(
                    {"type": "beacon.command_output", "correlation_id": "c1", "output": "second"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = VectorStore()
    svc = JournalService(voyage=_fixed_voyage(), store=store, events_path=path)
    assert await svc.refresh() == 2  # two outputs -> two entries, neither lost
    assert store.count() == 2


@pytest.mark.asyncio
async def test_refresh_embedding_failure_is_retried_not_lost(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                _envelope(
                    {"type": "beacon.command_input", "correlation_id": "c1", "raw": "whoami"}
                ),
                _envelope(
                    {"type": "beacon.command_output", "correlation_id": "c1", "output": "system"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    voyage = AsyncMock()
    voyage.embed_documents = AsyncMock(
        side_effect=[
            RuntimeError("voyage down"),  # first refresh fails mid-embed
            type("R", (), {"vectors": [[1.0, 0.0]], "total_tokens": 0})(),  # second succeeds
        ]
    )
    store = VectorStore()
    svc = JournalService(voyage=voyage, store=store, events_path=path)
    assert await svc.refresh() == 0  # embedding failed -> nothing added, does NOT raise
    assert store.count() == 0
    assert await svc.refresh() == 1  # same lines retried (offset not advanced) -> now indexed
    assert store.count() == 1
