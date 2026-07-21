from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.agent import run_interpretation_cycle
from redrobin.application.agents.assistant.parser import parse_interpretation
from redrobin.domain.assistant import Interpretation


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
