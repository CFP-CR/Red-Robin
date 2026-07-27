"""Slice 4 integration: reducer consumes bus events and updates working memory.

The reducer subscribes to ``EventBus`` and writes derived state into a
single-engagement ``WorkingMemoryRepo`` (Phase 1's JSON-backed repo). Each
case below publishes one event onto the bus and asserts the JSON working
memory reflects the change.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.working_memory_reducer import WorkingMemoryReducer
from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def _new_repo(tmp_path: Path, engagement_id: str) -> WorkingMemoryRepo:
    """Build a repo seeded with an empty working memory for the engagement."""
    repo = WorkingMemoryRepo(tmp_path / "working_memory.json")
    repo.save(WorkingMemory(engagement_id=engagement_id))
    return repo


async def _start_reducer(
    bus: EventBus, repo: WorkingMemoryRepo, engagement_id: str
) -> tuple[WorkingMemoryReducer, asyncio.Task[None]]:
    reducer = WorkingMemoryReducer(bus=bus, repo=repo, engagement_id=engagement_id)
    # Give the reducer a deterministic chance to subscribe before publishers run.
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(reducer.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return reducer, task


async def _drain(bus: EventBus, task: asyncio.Task[None]) -> None:
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_beacon_initial_writes_session(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-sess")
    _, task = await _start_reducer(bus, repo, "eng-sess")

    try:
        await bus.publish(
            {
                "type": "beacon.initial",
                "payload": {
                    "bid": "B-004",
                    "host": "DC01",
                    "user": "CORP\\admin",
                    "integrity_level": "high",
                },
            }
        )
        for _ in range(50):
            await asyncio.sleep(0.02)
            sessions = repo.load().sessions
            if sessions:
                break
        sessions = repo.load().sessions
        assert any(s.beacon_id == "B-004" and s.hostname == "DC01" for s in sessions)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_unknown_event_type_is_ignored(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-unknown")
    _, task = await _start_reducer(bus, repo, "eng-unknown")

    try:
        await bus.publish({"type": "x.unknown", "payload": {"n": 1}})
        await asyncio.sleep(0.1)
        wm = repo.load()
        assert wm.credentials == [] and wm.hosts == [] and wm.findings == []
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_beacon_command_output_is_ignored_by_reducer(tmp_path):
    """B2: the reducer no longer regex-extracts from command output — the LLM
    enricher is the sole extraction path (it publishes working_memory.deltas,
    a separate bus consumer). A command_output carrying NTLM / hostname /
    kerberos patterns must therefore produce NO creds / hosts / findings via
    the reducer."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-noextract")
    _, task = await _start_reducer(bus, repo, "eng-noextract")
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-001",
                    "output": (
                        "Username: CORP\\admin\n"
                        "NTLM: aad3b435b51404eeaad3b435b51404ee:"
                        "31d6cfe0d16ae931b73c59d7e0c089c0\n"
                        "Hostname: dc01\n"
                        "klist\nCached Tickets: (1)\n"
                    ),
                },
            }
        )
        await asyncio.sleep(0.2)
        wm = repo.load()
        assert wm.credentials == []
        assert wm.hosts == []
        assert wm.findings == []
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_reducer_republishes_snapshot_after_mutation(tmp_path):
    """#146: after handling a mutating event the reducer must publish a
    complete working_memory_snapshot back to the bus so the TUI updates live.
    Uses beacon.initial (a structured event) — the regex command_output path
    was retired in B2."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-reemit")
    # A second subscriber stands in for a connected TUI client.
    observer = await bus.subscribe()
    _, task = await _start_reducer(bus, repo, "eng-reemit")
    try:
        await bus.publish(
            {
                "type": "beacon.initial",
                "payload": {
                    "bid": "B-9",
                    "host": "DC01",
                    "user": "CORP\\admin",
                    "integrity_level": "high",
                },
            }
        )
        snapshot = None
        for _ in range(50):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except Exception:
                continue
            if evt.get("type") == "working_memory_snapshot":
                snapshot = evt
                break
        assert snapshot is not None, "reducer did not re-publish a snapshot"
        sessions = snapshot["payload"]["sessions"]
        assert any(s["beacon_id"] == "B-9" for s in sessions)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_lifespan_starts_reducer_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-test-001")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.reducer_task is not None
        assert not app.state.reducer_task.done()
        # Drive the live extraction path (enricher -> deltas -> reducer). The
        # enricher is off without an API key, so publish the deltas directly.
        await app.state.event_bus.publish(
            {
                "type": "working_memory.deltas",
                "payload": {
                    "source_beacon_id": "B-100",
                    "credentials": [
                        {
                            "domain": "CORP",
                            "username": "alice",
                            "secret_type": "plaintext",
                            "secret": "pw",
                        }
                    ],
                },
            }
        )
        await asyncio.sleep(0.3)
        creds = app.state.working_memory_repo.list_credentials()
        assert any(getattr(c, "username", None) == "alice" for c in creds)


@pytest.mark.asyncio
async def test_working_memory_deltas_event_merges_and_reemits(tmp_path):
    """A working_memory.deltas event is merged via apply_deltas + re-emits a snapshot."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-deltas")
    observer = await bus.subscribe()  # stands in for a connected TUI
    _, task = await _start_reducer(bus, repo, "eng-deltas")
    try:
        await bus.publish(
            {
                "type": "working_memory.deltas",
                "payload": {
                    "source_beacon_id": "B-7",
                    "hosts": [
                        {
                            "name": "ws01",
                            "os": "Windows 10",
                            "os_version": "10.0.19045",
                            "domain": "ACME",
                            "ip_addresses": ["10.0.0.5"],
                        }
                    ],
                    "credentials": [
                        {
                            "domain": "ACME",
                            "username": "jdoe",
                            "secret_type": "plaintext",
                            "secret": "pw",
                        }
                    ],
                    "findings": [{"kind": "gpp_password", "summary": "Groups.xml cpassword"}],
                },
            }
        )
        host = None
        for _ in range(50):
            await asyncio.sleep(0.02)
            hosts = repo.load().hosts
            if hosts:
                host = hosts[0]
                break
        assert host is not None and host.domain == "ACME"
        assert host.ip_addresses == ["10.0.0.5"]
        wm = repo.load()
        assert any(
            c.username == "jdoe" and c.secret_type == "plaintext" and c.source_beacon_id == "B-7"
            for c in wm.credentials
        )
        assert any(f.kind == "gpp_password" and f.source_beacon_id == "B-7" for f in wm.findings)

        # #146: a snapshot must be re-emitted so the TUI updates live.
        saw_snapshot = False
        for _ in range(50):
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.02)
                continue
            if evt.get("type") == "working_memory_snapshot":
                saw_snapshot = True
                break
        assert saw_snapshot, "reducer did not re-publish a snapshot after deltas merge"
    finally:
        await _drain(bus, task)
