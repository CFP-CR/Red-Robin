from __future__ import annotations

import asyncio

import pytest

from redrobin.application.agents.assistant.agent import run_answer_cycle
from redrobin.application.agents.assistant.assistant import Assistant
from redrobin.application.agents.assistant.parser import parse_answer
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo


def test_parse_answer_question_no_lesson():
    raw = '{"answer": "Kerberoast svc_sql then crack offline.", "citations": ["wm:svc_sql"], "lesson": null}'
    pa = parse_answer(raw)
    assert pa is not None
    assert pa.answer.startswith("Kerberoast")
    assert pa.citations == ["wm:svc_sql"]
    assert pa.lesson is None


def test_parse_answer_correction_captures_lesson():
    raw = (
        '{"answer": "Understood — I will prefer comsvcs.", "citations": [],'
        ' "lesson": {"scope": "operator", "context": "EDR observed",'
        ' "lesson": "prefer comsvcs minidump", "why": "operator says mimikatz too loud"}}'
    )
    pa = parse_answer(raw)
    assert pa is not None and pa.lesson is not None
    assert pa.lesson["scope"] == "operator"
    assert pa.lesson["lesson"] == "prefer comsvcs minidump"


def test_parse_answer_never_raises_on_garbage():
    assert parse_answer("not json") is None
    assert parse_answer('{"citations": 5}') is None  # no answer text
    assert parse_answer("42") is None  # valid JSON but not a dict
    assert parse_answer("") is None


def test_parse_answer_drops_malformed_lesson():
    # a lesson missing required fields is dropped (answer still returned)
    raw = '{"answer": "ok", "citations": [], "lesson": {"scope": "operator"}}'
    assert parse_answer(raw).lesson is None
    # a lesson with an out-of-vocab scope is also dropped
    bad_scope = '{"answer": "ok", "citations": [], "lesson": {"scope": "global", "context": "c", "lesson": "l", "why": "w"}}'
    assert parse_answer(bad_scope).lesson is None


@pytest.mark.asyncio
async def test_run_answer_cycle_sends_question_and_parses():
    seen = {}

    class _LLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            seen["user_message"] = user_message
            return '{"answer": "path: kerberoast -> DA", "citations": ["kb:kerberoast"], "lesson": null}'

    pa = await run_answer_cycle(
        llm=_LLM(),
        tools=[],
        wm_snapshot={"hosts": []},
        l5_summary="recon done",
        question="what's my path to DA?",
    )
    assert pa is not None and pa.answer == "path: kerberoast -> DA"
    assert "what's my path to DA?" in seen["user_message"]


class _AnswerLLM:
    def __init__(self, payload: str):
        self._payload = payload

    async def run(self, *, system, user_message, tools, max_tokens=2048):
        return self._payload


def _assistant(bus, llm, store, engagement="eng-A"):
    return Assistant(
        bus=bus,
        llm=llm,
        tools=[],
        trigger=TriggerGate(cooldown_sec=0.0, clock=lambda: 0.0),
        summary_reader=lambda: "recon done",
        enabled=True,
        lesson_store=store,
        engagement_provider=lambda: engagement,
    )


@pytest.mark.asyncio
async def test_operator_question_publishes_answer(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    llm = _AnswerLLM('{"answer": "kerberoast then crack", "citations": ["kb:kb"], "lesson": null}')
    a = _assistant(bus, llm, LongtermRepo(tmp_path / "lt.db"))
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.question", "payload": {"question_id": "q1", "text": "path to DA?"}}
    )
    ans = None
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            ans = evt
    assert ans is not None
    assert ans["payload"]["question_id"] == "q1"
    assert ans["payload"]["text"] == "kerberoast then crack"
    assert ans["payload"]["citations"] == ["kb:kb"]
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_operator_correction_captures_lesson_and_answers(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    store = LongtermRepo(tmp_path / "lt.db")
    llm = _AnswerLLM(
        '{"answer": "understood", "citations": [], "lesson": {"scope": "operator",'
        ' "context": "EDR", "lesson": "prefer comsvcs", "why": "too loud"}}'
    )
    a = _assistant(bus, llm, store)
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.question", "payload": {"question_id": "q2", "text": "that's too loud"}}
    )
    for _ in range(20):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.2)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            break
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
    lessons = store.list_operator_lessons(engagement_id="eng-A")
    assert len(lessons) == 1 and lessons[0].lesson == "prefer comsvcs"
    assert (
        lessons[0].engagement_id == "eng-A"
    )  # provenance attached from the provider, not the model


@pytest.mark.asyncio
async def test_operator_question_degraded_no_llm_is_silent(tmp_path):
    bus = EventBus()
    sub = await bus.subscribe()
    a = _assistant(bus, None, LongtermRepo(tmp_path / "lt.db"))
    a._llm = None
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish({"type": "operator.question", "payload": {"question_id": "q", "text": "hi"}})
    saw = False
    for _ in range(6):
        try:
            evt = await asyncio.wait_for(sub.get(), 0.05)
        except TimeoutError:
            break
        if evt.get("type") == "operator.answer":
            saw = True
    assert not saw
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_operator_question_survives_cycle_exception(tmp_path):
    # A raising LLM inside the Q/A cycle is logged + swallowed by the run-loop's
    # try/except around _on_question; the loop survives (never-crash, spec §6.E) —
    # the conversational counterpart of test_assistant_survives_cycle_exception.
    class _BoomLLM:
        async def run(self, *, system, user_message, tools, max_tokens=2048):
            raise RuntimeError("llm exploded")

    bus = EventBus()
    await bus.subscribe()
    a = _assistant(bus, _BoomLLM(), LongtermRepo(tmp_path / "lt.db"))
    started = asyncio.Event()
    a.on_started = started.set
    task = asyncio.create_task(a.run())
    await asyncio.wait_for(started.wait(), 1.0)
    await bus.publish(
        {"type": "operator.question", "payload": {"question_id": "q", "text": "boom?"}}
    )
    await asyncio.sleep(0.05)
    assert not task.done()  # the raising cycle was caught; the loop survives
    await bus.publish({"_type": "_close"})
    await asyncio.wait_for(task, 1.0)
