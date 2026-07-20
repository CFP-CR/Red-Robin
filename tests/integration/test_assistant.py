"""Integration: the assistant consumes enrichment.completed and publishes
advisor.recommendation (fake AgentLLM — no real API). Mirrors test_enricher.py."""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus


class _FakeAgentLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0
        self.last_user_message = ""

    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        self.calls += 1
        self.last_user_message = user_message
        return self._response


class _RaisingAgentLLM:
    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        raise RuntimeError("boom")


async def _start(bus, llm):
    assistant = Assistant(
        bus=bus,
        llm=llm,
        tools=build_tool_registry(None),
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done",
    )
    started = asyncio.Event()
    assistant.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(assistant.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return assistant, task


async def _drain(bus, task):
    await bus.close()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_assistant_publishes_recommendation_on_enrichment():
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _FakeAgentLLM(
        '{"suggested_action":"Validate svc_reports vs SQL01",'
        '"rationale":"a quiet bind beats a loud dump under CrowdStrike",'
        '"opsec_risk":"WARN","confidence":"high cred, unsure scope",'
        '"references":["type db_backup.config"]}'
    )
    _, task = await _start(bus, llm)
    try:
        # PRODUCTION ORDER: working_memory.deltas -> enrichment.completed ->
        # working_memory_snapshot. The assistant reacts on the MIDDLE event, so it must
        # forward-merge the deltas it just saw; the authoritative snapshot lands only
        # AFTER the cycle already ran (this order previously masked the staleness bug).
        await bus.publish(
            {
                "type": "working_memory.deltas",
                "payload": {
                    "credentials": [{"username": "svc_reports"}],
                    "defenses_observed": ["CrowdStrike"],
                },
            }
        )
        await bus.publish(
            {
                "type": "enrichment.completed",
                "payload": {"command": "type db_backup.config", "beacon_id": "B-7", "n_deltas": 2},
            }
        )
        await bus.publish(
            {
                "type": "working_memory_snapshot",
                "payload": {
                    "defenses_observed": ["CrowdStrike"],
                    "credentials": [{"username": "svc_reports"}],
                },
            }
        )
        rec_evt = None
        for _ in range(80):
            await asyncio.sleep(0.02)
            try:
                evt = observer.get_nowait()
            except asyncio.QueueEmpty:
                continue
            if evt.get("type") == "advisor.recommendation":
                rec_evt = evt
                break
        assert rec_evt is not None, "assistant did not publish advisor.recommendation"
        p = rec_evt["payload"]
        assert p["opsec_risk"] == "WARN"
        assert p["suggested_action"].startswith("Validate")
        assert p["references"]
        assert p["correlation_id"] == "B-7:type db_backup.config"
        # the cycle reasoned over FRESH working memory: the deltas published BEFORE
        # enrichment.completed were forward-merged into the push-context (not lost to
        # the stale snapshot, which only arrives afterwards).
        assert "svc_reports" in llm.last_user_message
        assert "CrowdStrike" in llm.last_user_message
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_degraded_without_llm_is_silent():
    bus = EventBus()
    observer = await bus.subscribe()
    _, task = await _start(bus, None)  # no llm -> degraded
    try:
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "whoami", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "advisor.recommendation" not in seen
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_survives_cycle_exception():
    bus = EventBus()
    await bus.subscribe()
    _, task = await _start(bus, _RaisingAgentLLM())
    try:
        await bus.publish(
            {"type": "working_memory_snapshot", "payload": {"hosts": [{"name": "ws01"}]}}
        )
        await bus.publish(
            {
                "type": "enrichment.completed",
                "payload": {"command": "systeminfo", "beacon_id": "B-1"},
            }
        )
        await asyncio.sleep(0.2)
        assert not task.done()  # the raising cycle was caught; the consumer is alive
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_disabled_flag_is_silent():
    # enabled=False must silence the assistant even with a working llm present —
    # distinct from the llm=None degraded path. The disabled guard short-circuits
    # BEFORE the cycle, so the llm is never called.
    bus = EventBus()
    observer = await bus.subscribe()
    llm = _FakeAgentLLM(
        '{"suggested_action":"x","rationale":"y","opsec_risk":"INFO",'
        '"confidence":"z","references":["r"]}'
    )
    assistant = Assistant(
        bus=bus,
        llm=llm,  # llm present, but the assistant is disabled
        tools=build_tool_registry(None),
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done",
        enabled=False,
    )
    started = asyncio.Event()
    assistant.on_started = started.set  # type: ignore[assignment]
    task = asyncio.create_task(assistant.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    try:
        await bus.publish(
            {"type": "working_memory_snapshot", "payload": {"hosts": [{"name": "ws01"}]}}
        )
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "whoami", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "advisor.recommendation" not in seen
        assert llm.calls == 0  # the disabled guard short-circuited before the cycle
    finally:
        await _drain(bus, task)


@pytest.mark.asyncio
async def test_assistant_silent_model_does_not_publish():
    bus = EventBus()
    observer = await bus.subscribe()
    _, task = await _start(bus, _FakeAgentLLM('{"speak": false}'))
    try:
        await bus.publish({"type": "working_memory_snapshot", "payload": {"hosts": []}})
        await bus.publish(
            {"type": "enrichment.completed", "payload": {"command": "sleep 5", "beacon_id": "B-1"}}
        )
        await asyncio.sleep(0.2)
        seen = []
        try:
            while True:
                seen.append(observer.get_nowait().get("type"))
        except asyncio.QueueEmpty:
            pass
        assert "advisor.recommendation" not in seen
    finally:
        await _drain(bus, task)
