from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.agent import run_answer_cycle
from redrobin.application.agents.assistant.parser import parse_answer


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
