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

    async def extract(self, *, command, beacon_id, output, host=None, user=None) -> str:
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


@pytest.mark.asyncio
async def test_enricher_recovers_command_via_correlation_id(tmp_path):
    """command_input then command_output with the same correlation_id → the
    enricher enriches with the recovered command (the stub records what it saw)."""
    bus = EventBus()
    await bus.subscribe()

    class _RecordingLLM:
        def __init__(self) -> None:
            self.seen_commands: list[str] = []

        async def extract(self, *, command, beacon_id, output, host=None, user=None) -> str:
            self.seen_commands.append(command)
            return "{}"

    llm = _RecordingLLM()
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_input",
                "payload": {
                    "bid": "B-7",
                    "operator": "juan",
                    "command": "shell",
                    "raw": "shell systeminfo",
                    "correlation_id": "18ff",
                },
            }
        )
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-7",
                    "task_id": "30",
                    "output": "Host Name: WS01\n" * 5,
                    "correlation_id": "18ff",
                },
            }
        )
        for _ in range(80):
            await asyncio.sleep(0.02)
            if llm.seen_commands:
                break
        assert llm.seen_commands == ["shell systeminfo"]
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_recovered_denylisted_command_is_skipped(tmp_path):
    """A bare output whose command is recovered as a denylisted verb → no LLM
    call (the denylist works now that the command is known)."""
    bus = EventBus()
    await bus.subscribe()
    llm = _StubLLM("{}")
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_input",
                "payload": {
                    "bid": "B-1",
                    "operator": "juan",
                    "command": "ls",
                    "raw": "ls -la /tmp",
                    "correlation_id": "aa11",
                },
            }
        )
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-1",
                    "task_id": "30",
                    "output": "a" * 50,
                    "correlation_id": "aa11",
                },
            }
        )
        await asyncio.sleep(0.2)
        assert llm.calls == 0  # recovered "ls -la /tmp" is denylisted -> skipped
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_rapid_fire_each_output_uses_its_own_command(tmp_path):
    """Two inputs then two outputs, interleaved by correlation_id (the §3 proof
    case): each output is enriched with ITS OWN command, not the last one."""
    bus = EventBus()
    await bus.subscribe()

    class _PairRecordingLLM:
        def __init__(self) -> None:
            # output -> command, so we can assert the pairing regardless of order
            self.pairs: dict[str, str] = {}

        async def extract(self, *, command, beacon_id, output, host=None, user=None) -> str:
            self.pairs[output.strip()] = command
            return "{}"

    llm = _PairRecordingLLM()
    _, task = await _start(bus, llm)
    try:
        # input A then input B (both queued before any output, like a sleep+trio)
        await bus.publish(
            {
                "type": "beacon.command_input",
                "payload": {
                    "bid": "B-1",
                    "operator": "j",
                    "command": "shell",
                    "raw": "shell whoami",
                    "correlation_id": "18ff",
                },
            }
        )
        await bus.publish(
            {
                "type": "beacon.command_input",
                "payload": {
                    "bid": "B-1",
                    "operator": "j",
                    "command": "shell",
                    "raw": "shell hostname",
                    "correlation_id": "272d",
                },
            }
        )
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-1",
                    "task_id": "30",
                    "output": "OUT_A" + "x" * 20,
                    "correlation_id": "18ff",
                },
            }
        )
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-1",
                    "task_id": "31",
                    "output": "OUT_B" + "y" * 20,
                    "correlation_id": "272d",
                },
            }
        )
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(llm.pairs) >= 2:
                break
        assert llm.pairs[("OUT_A" + "x" * 20).strip()] == "shell whoami"
        assert llm.pairs[("OUT_B" + "y" * 20).strip()] == "shell hostname"
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_self_annotation_only_output_skips_enrichment(tmp_path):
    """An output that is ONLY a [Red Robin] annotation strips to empty → below
    min_output_chars → no LLM call, no deltas."""
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _StubLLM('{"findings":[{"kind":"novel_pattern","summary":"x"}]}')
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-2",
                    "task_id": "30",
                    "output": "[Red Robin] novel pattern — Layer B reviewing async",
                },
            }
        )
        await asyncio.sleep(0.2)
        assert llm.calls == 0  # stripped to empty -> skipped before any LLM call
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
async def test_self_annotation_stripped_but_real_output_still_enriched(tmp_path):
    """A [Red Robin] line interleaved with real output → the annotation is
    stripped, the real output still reaches the LLM."""
    bus = EventBus()
    await bus.subscribe()

    class _RecordingLLM:
        def __init__(self) -> None:
            self.seen_outputs: list[str] = []

        async def extract(self, *, command, beacon_id, output, host=None, user=None) -> str:
            self.seen_outputs.append(output)
            return "{}"

    llm = _RecordingLLM()
    _, task = await _start(bus, llm)
    try:
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {
                    "bid": "B-3",
                    "task_id": "30",
                    "output": "Host Name: JCSP\n[Red Robin] novel pattern\nDomain: WORKGROUP",
                },
            }
        )
        for _ in range(80):
            await asyncio.sleep(0.02)
            if llm.seen_outputs:
                break
        assert llm.seen_outputs, "expected the stripped output to be enriched"
        assert "[Red Robin]" not in llm.seen_outputs[0]
        assert "Host Name: JCSP" in llm.seen_outputs[0]
        assert "Domain: WORKGROUP" in llm.seen_outputs[0]
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_semaphore_bounds_concurrent_llm_calls(tmp_path):
    """A multi-chunk output must never run more than max_concurrency extracts at
    once. A gating stub records peak in-flight; the enricher uses N=2."""
    bus = EventBus()
    await bus.subscribe()

    class _GatingLLM:
        def __init__(self) -> None:
            self.in_flight = 0
            self.peak = 0
            self._release = asyncio.Event()

        def release(self) -> None:
            self._release.set()

        async def extract(self, *, command, beacon_id, output, host=None, user=None) -> str:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                await self._release.wait()
            finally:
                self.in_flight -= 1
            return "{}"

    llm = _GatingLLM()
    enricher = WorkingMemoryEnricher(
        bus=bus,
        llm=llm,
        denylist=["ls", "pwd", "sleep"],
        min_output_chars=8,
        chunk_max_chars=16000,
        enabled=True,
        max_concurrency=2,
    )
    started = asyncio.Event()
    enricher.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(enricher.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    try:
        # ~60 KB of distinct lines -> chunk_output splits into many chunks (>2).
        big = "".join(f"line number {i} with some filler text here\n" for i in range(1500))
        await bus.publish(
            {
                "type": "beacon.command_output",
                "payload": {"bid": "B-1", "command": "systeminfo", "output": big},
            }
        )
        # Let the enricher fan chunks into the semaphore; with N=2 at most 2 run.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if llm.peak >= 2:
                break
        assert llm.peak == 2, f"peak in-flight was {llm.peak}, expected exactly 2"
        llm.release()  # let all chunks complete so the task can drain cleanly
        await asyncio.sleep(0.1)
    finally:
        await _drain(bus, task)


class _GroundingRecordingLLM:
    """EnricherLLM stub that records every extract() call and returns empty deltas."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def extract(
        self,
        *,
        command: str,
        beacon_id: str | None,
        output: str,
        host: str | None = None,
        user: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "command": command,
                "beacon_id": beacon_id,
                "output": output,
                "host": host,
                "user": user,
            }
        )
        return "{}"


class _NoopBus:
    async def publish(self, event: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_enricher_grounds_extract_with_beacon_host_user() -> None:
    llm = _GroundingRecordingLLM()
    enricher = WorkingMemoryEnricher(
        bus=_NoopBus(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        denylist=[],
        min_output_chars=0,
        chunk_max_chars=10000,
    )
    enricher._remember_session({"bid": "B-1", "host": "WS-FINANCE-03", "user": "CORP\\jdoe"})
    await enricher._enrich(
        {"bid": "B-1", "correlation_id": "c1", "output": "some command output here"}
    )

    assert llm.calls, "extract was never called"
    assert llm.calls[0]["host"] == "WS-FINANCE-03"
    assert llm.calls[0]["user"] == "CORP\\jdoe"


@pytest.mark.asyncio
async def test_enricher_grounding_is_none_for_unknown_beacon() -> None:
    llm = _GroundingRecordingLLM()
    enricher = WorkingMemoryEnricher(
        bus=_NoopBus(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        denylist=[],
        min_output_chars=0,
        chunk_max_chars=10000,
    )
    await enricher._enrich(
        {"bid": "B-9", "correlation_id": "c2", "output": "some command output here"}
    )
    assert llm.calls
    assert llm.calls[0]["host"] is None
    assert llm.calls[0]["user"] is None
