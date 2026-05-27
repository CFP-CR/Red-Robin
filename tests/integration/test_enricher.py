"""Integration: the enricher consumes beacon.command_output and publishes
working_memory.deltas + enrichment.completed (stub LLM — no real API)."""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.enrichment.enricher import WorkingMemoryEnricher
from redrobin.application.event_bus import EventBus


class _StubLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def extract(self, *, command, beacon_id, output) -> str:
        self.calls += 1
        return self._response


async def _start(bus, llm, **kw):
    enricher = WorkingMemoryEnricher(
        bus=bus,
        llm=llm,
        denylist=["ls", "pwd", "sleep"],
        min_output_chars=8,
        chunk_max_chars=16000,
        enabled=True,
        **kw,
    )
    started = asyncio.Event()
    enricher.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(enricher.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return enricher, task


async def _drain(bus, task):
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_enricher_publishes_deltas_and_completed(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM(
        '{"hosts":[{"name":"ws01","os":"Windows 10","domain":"ACME",'
        '"ip_addresses":["10.0.0.5"],"os_version":"10.0.19045"}],'
        '"credentials":[{"domain":"ACME","username":"jdoe","secret_type":"plaintext","secret":"pw"}],'
        '"findings":[{"kind":"gpp","summary":"x"}]}'
    )
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-7",
                    "command": "systeminfo",
                    "output": "Host Name: WS01\n" * 5,
                },
            }
        )
        deltas_evt = None
        completed_evt = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas":
                deltas_evt = evt
            elif evt.get("type") == "enrichment.completed":
                completed_evt = evt
            if deltas_evt and completed_evt:
                break
        assert deltas_evt is not None, "enricher did not publish working_memory.deltas"
        p = deltas_evt["payload"]
        assert p["source_beacon_id"] == "B-7"
        assert p["hosts"][0]["domain"] == "ACME"
        assert p["hosts"][0]["ip_addresses"] == ["10.0.0.5"]
        assert any(c["secret_type"] == "plaintext" for c in p["credentials"])
        assert completed_evt is not None
        assert completed_evt["payload"]["command"] == "systeminfo"
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_enricher_skips_denylisted_command(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM("{}")
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {"bid": "B-1", "command": "ls", "output": "a" * 50},
            }
        )
        await asyncio.sleep(0.2)
        assert llm.calls == 0  # denylisted -> no LLM call
        # no working_memory.deltas emitted
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "working_memory.deltas" not in seen
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_enricher_malformed_json_emits_failed_not_crash(tmp_path):
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM("this is not json")  # both the call + retry return junk
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {"bid": "B-9", "command": "systeminfo", "output": "x" * 50},
            }
        )
        failed = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "enrichment.failed":
                failed = evt
                break
        assert failed is not None, "expected enrichment.failed on unparseable output"
        assert llm.calls == 2  # one call + one retry
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_lifespan_starts_enricher_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-enr")
    # No ANTHROPIC_API_KEY -> enricher boots in degraded mode (llm None), task still runs.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.enricher_task is not None
        assert not app.state.enricher_task.done()

    # Clean teardown: the enricher drains via the bus _close sentinel (close_bus
    # runs before close_enricher), NOT via the 2.0s cancel fallback (invariant 2).
    assert app.state.enricher_task.done()
    assert app.state.enricher_task.cancelled() is False


@pytest.mark.asyncio
async def test_golden_systeminfo_carries_host_fields_149(tmp_path):
    """#149: a systeminfo extraction yields a host with os/os_version/domain/ips."""
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM(
        '{"hosts":[{"name":"EC2AMAZ-IJVFMFN","os":"Microsoft Windows Server 2019",'
        '"os_version":"10.0.17763 Build 17763","domain":"CORP",'
        '"ip_addresses":["10.100.0.21"]}]}'
    )
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-149",
                    "command": "shell systeminfo",
                    "output": "Host Name: EC2AMAZ-IJVFMFN\nDomain: CORP\n" + ("x\n" * 10),
                },
            }
        )
        host = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas" and evt["payload"]["hosts"]:
                host = evt["payload"]["hosts"][0]
                break
        assert host is not None
        assert host["domain"] == "CORP"
        assert host["os_version"] == "10.0.17763 Build 17763"
        assert host["ip_addresses"] == ["10.100.0.21"]
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_golden_klist_zero_tickets_no_finding_145(tmp_path):
    """#145: klist with 'Cached Tickets: (0)' yields NO kerberos finding (LLM returns [])."""
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM('{"findings":[]}')  # the grounded LLM emits no finding for (0)
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-145",
                    "command": "klist",
                    "output": "Current LogonId is 0:0x12345\nCached Tickets: (0)\n",
                },
            }
        )
        deltas = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "working_memory.deltas":
                deltas = evt["payload"]
                break
        assert deltas is not None
        assert deltas["findings"] == []  # no spurious kerberos finding
    finally:
        await _drain(bus, task)
