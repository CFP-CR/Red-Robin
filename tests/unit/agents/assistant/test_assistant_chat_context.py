from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.agent import build_answer_context, build_push_context
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus

_REC_JSON = (
    '{"suggested_action":"rotate to comsvcs","rationale":"loud EDR",'
    '"opsec_risk":"WARN","confidence":"medium"}'
)
_HISTORY = "[operator] path to DA?\n[assistant] Kerberoast svc_sql"


class _CapturingLLM:
    """Records every user_message it is asked to run, and answers plausibly."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        self.messages.append(user_message)
        if "The operator says" in user_message:
            return '{"answer": "kerberoast then crack", "lesson": null}'
        if "SHARED PICTURE" in system or "milestone_id" in system:
            return '{"phase": "recon", "milestone_id": ""}'
        return _REC_JSON


def _assistant(bus, llm, *, history_reader):
    return Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 1000.0),
        summary_reader=lambda: "recon done",
        history_reader=history_reader,
        enabled=True,
        clock=lambda: 1000.0,
    )


def test_push_context_emits_the_conversation_section():
    ctx = build_push_context(
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="whoami",
        chat_history=_HISTORY,
    )
    assert "# Conversation so far" in ctx
    assert "Kerberoast svc_sql" in ctx


def test_push_context_without_history_says_so():
    ctx = build_push_context(wm_snapshot={}, l5_summary="", trigger_command="whoami")
    assert "# Conversation so far" in ctx
    assert "(no conversation yet)" in ctx


def test_answer_context_emits_the_conversation_section():
    ctx = build_answer_context(
        wm_snapshot={}, l5_summary="", question="hacé el paso 1", chat_history=_HISTORY
    )
    assert "# Conversation so far" in ctx
    assert "Kerberoast svc_sql" in ctx


@pytest.mark.asyncio
async def test_conversational_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._on_question({"text": "hacé el paso 1", "question_id": "opq-0"})
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_reactive_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._on_enrichment({"command": "whoami", "beacon_id": "b1"})
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_proactive_cycle_receives_the_history():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert any("Kerberoast svc_sql" in m for m in llm.messages)


@pytest.mark.asyncio
async def test_interpretation_cycle_does_NOT_receive_the_history():
    # the interpretation cycle WRITES engagement state; chat must never drive a state
    # write, so its prompt must not carry the conversation (spec §5.5)
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=lambda: _HISTORY)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._safe_interpret()
    assert llm.messages, "the interpretation cycle should have run"
    assert all("Kerberoast svc_sql" not in m for m in llm.messages)


@pytest.mark.asyncio
async def test_cycle_survives_a_raising_history_reader():
    def _boom() -> str:
        raise RuntimeError("reader exploded")

    bus = EventBus()
    sub = await bus.subscribe()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=_boom)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")  # must not raise
    evt = await asyncio.wait_for(sub.get(), 1.0)
    while evt.get("type") != "advisor.recommendation":
        evt = await asyncio.wait_for(sub.get(), 1.0)
    assert evt["payload"]["suggested_action"] == "rotate to comsvcs"


@pytest.mark.asyncio
async def test_no_history_reader_keeps_prior_behaviour():
    bus = EventBus()
    llm = _CapturingLLM()
    a = _assistant(bus, llm, history_reader=None)
    a._wm_snapshot = {"defenses_observed": ["CrowdStrike"]}
    await a._maybe_proactive("test")
    assert any("(no conversation yet)" in m for m in llm.messages)
