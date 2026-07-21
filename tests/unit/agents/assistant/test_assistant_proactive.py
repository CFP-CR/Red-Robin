from __future__ import annotations

import asyncio
import time

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"CrowdStrike is loud",'
    '"opsec_risk":"WARN","confidence":"medium","references":["wm"]}'
)
_UNSET = object()  # sentinel so llm=None yields Assistant(llm=None), not the default fake


class _FramingLLM:
    """Speaks (returns a rec) ONLY for the proactive framing; silent otherwise —
    lets a test isolate a proactive utterance from a reactive one."""

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        if "Proactive check" in user_message:
            return _REC_JSON
        return '{"speak": false}'


def _assistant(bus, clock, *, lull_sec=300.0, proactive_min_interval_sec=600.0, llm=_UNSET):
    return Assistant(
        bus=bus,
        llm=_FramingLLM() if llm is _UNSET else llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=clock),
        summary_reader=lambda: "recon done",
        enabled=True,
        clock=clock,
        lull_sec=lull_sec,
        proactive_min_interval_sec=proactive_min_interval_sec,
    )


@pytest.mark.asyncio
async def test_maybe_proactive_publishes_when_novel_and_rate_ok():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    evt = sub.get_nowait()
    assert evt["type"] == "advisor.recommendation"
    assert evt["payload"]["correlation_id"] is None


@pytest.mark.asyncio
async def test_proactive_rate_limited():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0], proactive_min_interval_sec=600.0)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("first")
    assert sub.get_nowait()["type"] == "advisor.recommendation"
    # a change in state (novel) but within N -> still suppressed by the rate-limit
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike", "Defender"]}
    t[0] = 1000.0 + 599.0
    await a._maybe_proactive("second")
    assert sub.empty()
    # past N -> allowed again
    t[0] = 1000.0 + 601.0
    await a._maybe_proactive("third")
    assert sub.get_nowait()["type"] == "advisor.recommendation"


@pytest.mark.asyncio
async def test_proactive_silent_on_unchanged_state_novelty_gate():
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("first")
    assert sub.get_nowait()["type"] == "advisor.recommendation"
    # unchanged state, well past the rate-limit -> the SAME gate's novelty stays silent
    t[0] = 1_000_000.0
    await a._maybe_proactive("again")
    assert sub.empty()


@pytest.mark.asyncio
async def test_proactive_degraded_no_llm_is_silent():
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, lambda: 0.0, llm=None)  # Assistant(llm=None) — degraded
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert sub.empty()


@pytest.mark.asyncio
async def test_material_change_backstops_when_reactive_silent():
    # A new defence lands (material). The FramingLLM is silent for reactive
    # (enrichment) framing but speaks for proactive -> the backstop fires.
    bus = EventBus()
    sub = await bus.subscribe()
    t = [1000.0]
    a = _assistant(bus, lambda: t[0])
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())  # on_started assigned BEFORE create_task
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "working_memory.deltas", "payload": {"defenses_observed": ["CrowdStrike"]}}
    )
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}}
    )
    # drain: expect EXACTLY ONE card — the proactive backstop (reactive stayed silent),
    # never both for the same state.
    recs = []
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "advisor.recommendation":
            recs.append(evt)
    assert len(recs) == 1
    assert recs[0]["payload"]["correlation_id"] is None
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_material_backstop_does_not_fire_on_re_observed_defense():
    # A defence already in the snapshot, re-observed, must NOT arm the material
    # backstop (D3 — material = a NEW defence). Reactive stays silent (FramingLLM),
    # so the correct outcome is NO advisor.recommendation at all.
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, lambda: 1000.0)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}  # already known
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "working_memory.deltas", "payload": {"defenses_observed": ["CrowdStrike"]}}
    )
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}}
    )
    saw_rec = False
    for _ in range(10):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.05)
        except TimeoutError:
            break
        if evt.get("type") == "advisor.recommendation":
            saw_rec = True
    assert not saw_rec
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_lull_fires_after_idle():
    # Small real lull so the wait_for timeout elapses fast; after a beat with no
    # further events, the lull proactive fires.
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, time.monotonic, lull_sec=0.05, proactive_min_interval_sec=0.0)
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())  # on_started assigned BEFORE create_task
    await asyncio.wait_for(started.wait(), 1.0)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "ls", "beacon_id": "b1"}}
    )
    rec = None
    for _ in range(50):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.1)
        except TimeoutError:
            continue
        if evt.get("type") == "advisor.recommendation":
            rec = evt
            break
    assert rec is not None and rec["payload"]["correlation_id"] is None
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_run_loop_survives_malformed_deltas():
    # A malformed working_memory.deltas payload (an unhashable entry in
    # defenses_observed makes the new-defence `any(... not in set)` check raise) must
    # be logged + swallowed by the deltas branch, never crash the run loop
    # (never-crash, spec §6.E) — the deltas counterpart of
    # test_proactive_survives_cycle_exception. Without the guard, run() escapes the
    # while loop, the finally unsubscribes, and the assistant task dies silently.
    bus = EventBus()
    a = _assistant(bus, lambda: 1000.0)
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {
            "type": "working_memory.deltas",
            "payload": {"defenses_observed": [{"unhashable": "dict"}]},
        }
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # the malformed payload did NOT crash the loop
    # a following benign event is still processed -> the loop exited cleanly
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_proactive_survives_cycle_exception():
    # A raising LLM inside a proactive cycle is logged and swallowed by
    # _safe_proactive; the run loop survives (never-crash, spec §6.E) — the
    # proactive counterpart of test_assistant_survives_cycle_exception.
    class _BoomLLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            if "Proactive check" in user_message:
                raise RuntimeError("llm exploded")  # only the proactive cycle raises
            return '{"speak": false}'  # reactive stays silent

    bus = EventBus()
    a = _assistant(bus, lambda: 1000.0, llm=_BoomLLM())
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "working_memory.deltas", "payload": {"defenses_observed": ["CrowdStrike"]}}
    )
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}}
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # the proactive exception did NOT crash the loop
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
