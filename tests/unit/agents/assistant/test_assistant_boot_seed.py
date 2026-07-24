from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus


def _assistant(bus, *, snapshot_reader):
    return Assistant(
        bus=bus,
        llm=None,  # degraded — no cycles; we only exercise the boot seed
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "",
        enabled=True,
        clock=lambda: 0.0,
        snapshot_reader=snapshot_reader,
    )


async def _boot(a):
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    return task


async def _close(bus, task):
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_run_seeds_wm_snapshot_from_reader_at_boot():
    # #30: the assistant hydrates its L2 snapshot from the persisted WM at boot, so it is
    # NOT blind to existing engagement state (objectives/creds/phase) before the first
    # mutating bus event. Proven by NOT publishing any event.
    bus = EventBus()
    seed = {"current_phase": "lateral_movement", "objectives": [{"id": "obj-1"}]}
    a = _assistant(bus, snapshot_reader=lambda: seed)
    task = await _boot(a)
    assert a._wm_snapshot == seed  # seeded before the loop, no bus event needed
    await _close(bus, task)


@pytest.mark.asyncio
async def test_run_survives_snapshot_seed_failure():
    # a failing repo read at boot must NEVER crash the assistant (never-crash idiom)
    bus = EventBus()

    def _boom():
        raise RuntimeError("repo read boom")

    a = _assistant(bus, snapshot_reader=_boom)
    task = await _boot(a)  # boot survived the raising reader
    assert a._wm_snapshot == {}  # stayed empty, did not crash
    await _close(bus, task)


@pytest.mark.asyncio
async def test_run_without_snapshot_reader_starts_empty():
    # backwards-compat: no reader -> the pre-#30 behavior (empty until the first event)
    bus = EventBus()
    a = _assistant(bus, snapshot_reader=None)
    task = await _boot(a)
    assert a._wm_snapshot == {}
    await _close(bus, task)
