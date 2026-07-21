from __future__ import annotations

import pytest

from redrobin.application.agents.assistant.agent import run_interpretation_cycle
from redrobin.application.agents.assistant.interpret import (
    is_own_proposal_question,
    milestone_events,
    resolve_confirm,
)
from redrobin.application.agents.assistant.parser import parse_interpretation
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
