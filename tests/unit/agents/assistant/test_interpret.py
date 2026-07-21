from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.agent import run_interpretation_cycle
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.interpret import (
    is_own_proposal_question,
    milestone_events,
    resolve_confirm,
)
from redrobin.application.agents.assistant.parser import parse_interpretation
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.domain.assistant import Interpretation
from redrobin.domain.memory import PendingMilestone


def test_parse_soft_interpretation_no_confirm():
    raw = '{"phase": "lateral-movement", "milestone_id": "obj1", "status": "in_progress", "progress_pct": 40, "needs_confirm": false, "prompt": ""}'
    it = parse_interpretation(raw)
    assert it == Interpretation("lateral-movement", "obj1", "in_progress", 40, False, "")


def test_parse_commit_interpretation_needs_confirm():
    raw = '{"phase": "", "milestone_id": "obj1", "status": "COMPLETE", "progress_pct": 100, "needs_confirm": true, "prompt": "Objective obj1 done?"}'
    it = parse_interpretation(raw)
    assert it is not None and it.needs_confirm is True and it.prompt == "Objective obj1 done?"


def test_parse_interpretation_none_when_no_change():
    assert parse_interpretation('{"interpretation": null}') is None
    assert (
        parse_interpretation('{"needs_confirm": false}') is None
    )  # no milestone_id/phase -> nothing to write
    assert parse_interpretation("not json") is None
    assert parse_interpretation("42") is None
    assert parse_interpretation("") is None


def test_parse_clamps_progress_and_defaults():
    raw = '{"phase": "recon", "milestone_id": "", "status": "in_progress", "progress_pct": 250, "needs_confirm": false}'
    it = parse_interpretation(raw)
    assert it is not None and it.progress_pct == 100  # clamped 0..100
    assert it.prompt == ""  # defaulted


def test_parse_non_coercible_progress_is_zero():
    it = parse_interpretation(
        '{"milestone_id": "o", "status": "x", "progress_pct": {"bad": 1}, "needs_confirm": false}'
    )
    assert it is not None and it.progress_pct == 0  # int() TypeError guarded, never raises


def test_parse_confirm_without_milestone_id_downgrades_to_soft():
    # a confirm needs an id to be trackable — without one it becomes a soft auto-write
    it = parse_interpretation(
        '{"phase": "recon", "milestone_id": "", "status": "x", "progress_pct": 50, "needs_confirm": true}'
    )
    assert it is not None and it.needs_confirm is False


@pytest.mark.asyncio
async def test_run_interpretation_cycle_parses():
    class _LLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            return '{"phase": "recon", "milestone_id": "obj1", "status": "in_progress", "progress_pct": 30, "needs_confirm": false, "prompt": ""}'

    it = await run_interpretation_cycle(
        llm=_LLM(), tools=[], wm_snapshot={"objectives": []}, l5_summary=""
    )
    assert it is not None and it.phase == "recon"


def test_soft_interpretation_emits_one_commit_milestone():
    it = Interpretation("recon", "obj1", "in_progress", 40, False, "")
    evts = milestone_events(it)
    assert len(evts) == 1
    assert evts[0]["type"] == "engagement.milestone"
    assert evts[0]["payload"]["milestone_id"] == "obj1"
    assert not evts[0]["payload"].get("pending")


def test_confirm_interpretation_emits_pending_milestone_and_question():
    it = Interpretation("", "obj1", "COMPLETE", 100, True, "Objective obj1 done?")
    evts = milestone_events(it)
    types = [e["type"] for e in evts]
    assert types == ["engagement.milestone", "operator.question"]
    assert evts[0]["payload"]["pending"] is True
    assert evts[1]["payload"]["context_refs"] == ["milestone:obj1"]
    assert evts[1]["payload"]["text"] == "Objective obj1 done?"


def test_resolve_confirm_yes_commits():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    evt = resolve_confirm(
        answer_payload={"question_id": "obj1", "text": "yes, do it"}, pending_milestones=pend
    )
    assert evt is not None and evt["type"] == "engagement.milestone"
    assert evt["payload"]["milestone_id"] == "obj1" and not evt["payload"].get("pending")
    assert not evt["payload"].get("drop")


def test_resolve_confirm_no_drops():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    evt = resolve_confirm(
        answer_payload={"question_id": "obj1", "text": "no, not yet"}, pending_milestones=pend
    )
    assert evt is not None and evt["payload"]["drop"] is True


def test_resolve_confirm_unmatched_answer_is_ignored():
    assert (
        resolve_confirm(
            answer_payload={"question_id": "other", "text": "yes"}, pending_milestones=[]
        )
        is None
    )


def test_resolve_confirm_empty_qid_never_matches():
    # an operator ask carries no question_id (-> "") — it must NOT collide with an empty-id pending
    pend = [PendingMilestone("", "p", "COMPLETE", 100, "?")]
    assert (
        resolve_confirm(answer_payload={"question_id": "", "text": "yes"}, pending_milestones=pend)
        is None
    )


def test_resolve_confirm_negation_wins_and_ambiguous_leaves_pending():
    pend = [PendingMilestone("obj1", "lateral", "COMPLETE", 100, "?")]
    # "no, not yet" -> drop (the "y" in "yet" must NOT read as affirmative)
    assert (
        resolve_confirm(
            answer_payload={"question_id": "obj1", "text": "no, not yet"}, pending_milestones=pend
        )["payload"]["drop"]
        is True
    )
    # ambiguous -> None (leave the pending alive; never guess a commit)
    assert (
        resolve_confirm(
            answer_payload={"question_id": "obj1", "text": "hmm, interesting"},
            pending_milestones=pend,
        )
        is None
    )


def test_is_own_proposal_question():
    assert is_own_proposal_question({"context_refs": ["milestone:obj1"]}) is True
    assert is_own_proposal_question({"context_refs": ["host:DC01"]}) is False
    assert is_own_proposal_question({"text": "what's my path to DA?"}) is False


class _InterpretLLM:
    def __init__(self, payload):
        self._p = payload

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        # reactive/answer cycles stay silent; the interpretation cycle returns the milestone
        if "should the shared picture be updated" in user_message:
            return self._p
        return '{"speak": false}'


def _assistant(bus, llm, snapshot=None):
    a = Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done",
        enabled=True,
    )
    a._wm_snapshot = snapshot if snapshot is not None else {"objectives": [{"id": "obj1"}]}
    return a


@pytest.mark.asyncio
async def test_soft_interpretation_publishes_commit_milestone(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    llm = _InterpretLLM(
        '{"phase":"recon","milestone_id":"obj1","status":"in_progress","progress_pct":30,"needs_confirm":false,"prompt":""}'
    )
    a = _assistant(bus, llm)
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "seatbelt", "beacon_id": "b1"}}
    )
    got = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "engagement.milestone":
            got = evt
    assert (
        got is not None
        and got["payload"]["milestone_id"] == "obj1"
        and not got["payload"].get("pending")
    )
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_confirm_answer_commits_pending(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(
        bus,
        _InterpretLLM('{"speak": false}'),
        snapshot={
            "objectives": [{"id": "obj1"}],
            "pending_milestones": [
                {
                    "milestone_id": "obj1",
                    "phase": "lateral",
                    "status": "COMPLETE",
                    "progress_pct": 100,
                    "prompt": "?",
                }
            ],
        },
    )
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.answer", "payload": {"question_id": "obj1", "text": "yes"}}
    )
    got = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "engagement.milestone":
            got = evt
    assert (
        got is not None
        and got["payload"]["milestone_id"] == "obj1"
        and not got["payload"].get("pending")
    )
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_interpretation_survives_cycle_exception(tmp_path):
    class _Boom:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            if "should the shared picture be updated" in user_message:
                raise RuntimeError("boom")
            return '{"speak": false}'

    bus = EventBus()
    await bus.subscribe()
    a = _assistant(bus, _Boom())
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "enrichment.completed", "payload": {"command": "x", "beacon_id": "b1"}}
    )
    await asyncio.sleep(0.05)
    assert not task.done()
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_confirm_malformed_pending_survives(tmp_path):
    # a malformed pending dict (missing fields) in the cache is skipped, not crashed
    bus = EventBus()
    await bus.subscribe()
    a = _assistant(
        bus,
        _InterpretLLM('{"speak": false}'),
        snapshot={"objectives": [], "pending_milestones": [{"milestone_id": "obj1"}]},
    )
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.answer", "payload": {"question_id": "obj1", "text": "yes"}}
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # PendingMilestone(**p) TypeError skipped, loop survives
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
