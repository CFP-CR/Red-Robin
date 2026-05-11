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
async def test_beacon_output_with_ntlm_writes_credential(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-ntlm")
    _, task = await _start_reducer(bus, repo, "eng-ntlm")

    try:
        await bus.publish(
            {
                "type": "c2.beacon_output",
                "payload": {
                    "beacon_id": "B-001",
                    "output": (
                        "Username: CORP\\admin\n"
                        "NTLM: aad3b435b51404eeaad3b435b51404ee:"
                        "31d6cfe0d16ae931b73c59d7e0c089c0\n"
                    ),
                },
            }
        )
        # Drive the loop so the reducer dequeues + writes.
        for _ in range(50):
            await asyncio.sleep(0.02)
            creds = repo.list_credentials()
            if creds:
                break
        creds = repo.list_credentials()
        assert any(c.username == "admin" and c.domain == "CORP" for c in creds)
        assert all(c.source_beacon_id == "B-001" for c in creds)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_beacon_output_hostname_writes_host(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-host")
    _, task = await _start_reducer(bus, repo, "eng-host")

    try:
        await bus.publish(
            {
                "type": "c2.beacon_output",
                "payload": {
                    "beacon_id": "B-002",
                    "output": "Hostname: dc01\n",
                },
            }
        )
        for _ in range(50):
            await asyncio.sleep(0.02)
            hosts = repo.load().hosts
            if hosts:
                break
        hosts = repo.load().hosts
        assert any(h.name == "DC01" for h in hosts)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_beacon_output_kerberos_appends_finding(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-krb")
    _, task = await _start_reducer(bus, repo, "eng-krb")

    try:
        await bus.publish(
            {
                "type": "c2.beacon_output",
                "payload": {
                    "beacon_id": "B-003",
                    "output": "klist\nCached Tickets: (1)\n   Server: krbtgt/CORP\n",
                },
            }
        )
        for _ in range(50):
            await asyncio.sleep(0.02)
            findings = repo.load().findings
            if findings:
                break
        findings = repo.load().findings
        assert any(f.kind == "kerberos_ticket_observed" for f in findings)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_beacon_checkin_writes_session(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-sess")
    _, task = await _start_reducer(bus, repo, "eng-sess")

    try:
        await bus.publish(
            {
                "type": "c2.beacon_checkin",
                "payload": {
                    "beacon_id": "B-004",
                    "hostname": "DC01",
                    "user": "CORP\\admin",
                    "integrity": "high",
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
async def test_opsec_block_verdict_appends_finding(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-block")
    _, task = await _start_reducer(bus, repo, "eng-block")

    try:
        await bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {
                    "verdict": "BLOCK",
                    "command": "mimikatz",
                    "reason": "high signal",
                    "beacon_id": "B-005",
                },
            }
        )
        for _ in range(50):
            await asyncio.sleep(0.02)
            findings = repo.load().findings
            if findings:
                break
        findings = repo.load().findings
        assert any(f.kind == "opsec_block" for f in findings)
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_opsec_allow_verdict_is_ignored(tmp_path):
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-allow")
    _, task = await _start_reducer(bus, repo, "eng-allow")

    try:
        await bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {"verdict": "ALLOW", "command": "ls"},
            }
        )
        await asyncio.sleep(0.1)
        assert repo.load().findings == []
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
async def test_lifespan_starts_reducer_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-test-001")

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.reducer_task is not None
        assert not app.state.reducer_task.done()
        # Publish a beacon output with creds; reducer should write to working memory.
        await app.state.event_bus.publish({
            "type": "c2.beacon_output",
            "payload": {
                "beacon_id": "B-100",
                "output": "Username: CORP\\alice\nNTLM: aad3b435b51404eeaad3b435b51404ee:11111111111111111111111111111111",
            },
        })
        # Give reducer a tick to process; tighter pause is fine since the bus
        # is local and the repo is sync.
        await asyncio.sleep(0.3)
        creds = app.state.working_memory_repo.list_credentials()
        assert any(getattr(c, "username", None) == "alice" for c in creds)
