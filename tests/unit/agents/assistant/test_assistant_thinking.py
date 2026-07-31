from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"loud EDR",'
    '"opsec_risk":"WARN","confidence":"medium","references":["wm"]}'
)


class _RecLLM:
    """Returns a valid recommendation for any cycle (so the wrapped cycle publishes)."""

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        return _REC_JSON


def _assistant(bus, *, llm):
    return Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 1000.0),
        summary_reader=lambda: "recon done",
        enabled=True,
        clock=lambda: 1000.0,
    )


@pytest.mark.asyncio
async def test_thinking_brackets_the_llm_cycle():
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, llm=_RecLLM())
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    evts = []
    while not sub.empty():
        evts.append(sub.get_nowait())
    # start brackets BEFORE the model call, stop AFTER it, then the recommendation lands
    assert [e["type"] for e in evts] == [
        "assistant.thinking",
        "assistant.thinking",
        "advisor.recommendation",
    ]
    assert evts[0]["payload"]["state"] == "start"
    assert evts[1]["payload"]["state"] == "stop"


@pytest.mark.asyncio
async def test_thinking_emit_failure_never_breaks_the_cycle():
    class _ThinkBoomBus:
        def __init__(self):
            self.published = []

        async def publish(self, evt):
            if evt.get("type") == "assistant.thinking":
                raise RuntimeError("thinking publish boom")
            self.published.append(evt)

    bus = _ThinkBoomBus()
    a = _assistant(bus, llm=_RecLLM())
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    # a raising thinking-publish is swallowed; the cycle still completes + publishes its rec
    await a._maybe_proactive("test")
    assert any(e["type"] == "advisor.recommendation" for e in bus.published)
