import copy

import pytest

from redrobin.application.agents.assistant.agent import (
    apply_deltas_to_snapshot,
    build_push_context,
    build_system_prompt,
    cap_wm_snapshot,
    run_cycle,
    state_signature,
)
from redrobin.application.agents.assistant.tools import build_tool_registry
from redrobin.domain.events import Severity


def test_system_prompt_names_the_five_parts_and_the_thesis():
    p = build_system_prompt()
    for field in ("suggested_action", "rationale", "opsec_risk", "confidence", "references"):
        assert field in p
    assert "ADVISORY ONLY" in p
    assert "speak" in p  # the stay-silent sentinel is documented


def test_push_context_carries_l2_l5_and_trigger():
    ctx = build_push_context(
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon complete",
        trigger_command="type db_backup.config",
    )
    assert "CrowdStrike" in ctx
    assert "recon complete" in ctx
    assert "type db_backup.config" in ctx


def test_state_signature_changes_with_state():
    assert state_signature({"hosts": []}) != state_signature({"hosts": [{"name": "ws01"}]})


def test_push_context_caps_working_memory_per_category_without_mutating():
    # spec §6.B refinement 2 (context-rot): only the most-recent K items of each
    # list category are pushed to the model; the cached snapshot is never altered.
    snapshot = {
        "hosts": [{"name": f"h{i}"} for i in range(30)],  # > K -> capped
        "credentials": [{"username": f"u{i}"} for i in range(3)],  # < K -> untouched
        "current_phase": "lateral-movement",  # non-list scalar -> untouched
    }
    original = copy.deepcopy(snapshot)
    ctx = build_push_context(
        wm_snapshot=snapshot, l5_summary="", trigger_command="whoami", wm_max_items=10
    )
    # only the last 10 hosts (the newest — enricher appends) survive
    assert '"h29"' in ctx and '"h20"' in ctx
    assert '"h19"' not in ctx and '"h0"' not in ctx
    # a small category is passed through unchanged
    assert '"u0"' in ctx and '"u2"' in ctx
    # the scalar field is preserved
    assert "lateral-movement" in ctx
    # the cached snapshot is NOT mutated (non-mutating copy, like apply_deltas)
    assert snapshot == original


def test_cap_wm_snapshot_returns_new_dict_and_lists():
    snapshot = {"findings": [1, 2, 3, 4, 5], "tools_used": ["a"]}
    capped = cap_wm_snapshot(snapshot, 2)
    assert capped["findings"] == [4, 5]  # last K (tail = newest)
    assert capped["tools_used"] == ["a"]  # under the cap -> unchanged
    # non-mutating: a new dict and a new capped list
    assert capped is not snapshot
    assert capped["findings"] is not snapshot["findings"]
    assert snapshot["findings"] == [1, 2, 3, 4, 5]


def test_apply_deltas_extends_snapshot_lists_without_mutating_input():
    snapshot = {"credentials": [{"username": "svc_web"}], "defenses_observed": ["Defender"]}
    deltas = {
        "credentials": [{"username": "svc_reports"}],
        "defenses_observed": ["CrowdStrike"],
        "hosts": [{"name": "SQL01"}],
    }
    merged = apply_deltas_to_snapshot(snapshot, deltas)
    # existing snapshot lists are EXTENDED with the deltas (shallow, order-preserving)
    assert [c["username"] for c in merged["credentials"]] == ["svc_web", "svc_reports"]
    assert merged["defenses_observed"] == ["Defender", "CrowdStrike"]
    # a delta key absent from the snapshot is added
    assert merged["hosts"] == [{"name": "SQL01"}]
    # the input snapshot is NOT mutated (new dict + new lists)
    assert snapshot == {"credentials": [{"username": "svc_web"}], "defenses_observed": ["Defender"]}


def test_apply_deltas_handles_missing_and_empty_keys():
    assert apply_deltas_to_snapshot({}, {}) == {}
    # a delta key with no snapshot counterpart merges cleanly onto an empty base
    assert apply_deltas_to_snapshot({}, {"hosts": [{"name": "ws01"}]}) == {
        "hosts": [{"name": "ws01"}]
    }
    # empty delta lists are skipped — the snapshot is returned unchanged (by value)
    assert apply_deltas_to_snapshot({"hosts": [{"name": "ws01"}]}, {"hosts": []}) == {
        "hosts": [{"name": "ws01"}]
    }


class _FakeAgentLLM:
    """Exercises the kb_retrieve tool (proving the wiring) then returns canned JSON."""

    def __init__(self, response: str):
        self._response = response
        self.seen_tools: list[str] = []

    async def run(self, *, system, user_message, tools, max_tokens=2048) -> str:
        self.seen_tools = [t.name for t in tools]
        for t in tools:
            if t.name == "kb_retrieve":
                await t.handler({"query": "quiet cred validation"})
        return self._response


@pytest.mark.asyncio
async def test_run_cycle_returns_parsed_recommendation_and_wires_tools():
    llm = _FakeAgentLLM(
        '{"suggested_action":"Validate svc_reports vs SQL01",'
        '"rationale":"a quiet bind beats a loud dump under CrowdStrike",'
        '"opsec_risk":"WARN","confidence":"high cred, unsure scope",'
        '"references":["vault/mssql.md#chunk_0"]}'
    )
    rec = await run_cycle(
        llm=llm,
        tools=build_tool_registry(None),
        wm_snapshot={"defenses_observed": ["CrowdStrike"]},
        l5_summary="recon done",
        trigger_command="type db_backup.config",
        correlation_id="18ff",
    )
    assert rec is not None
    assert rec.opsec_risk is Severity.WARN
    assert rec.correlation_id == "18ff"
    assert "kb_retrieve" in llm.seen_tools  # the tool set was wired into the cycle


@pytest.mark.asyncio
async def test_run_cycle_silent_returns_none():
    llm = _FakeAgentLLM('{"speak": false}')
    rec = await run_cycle(
        llm=llm,
        tools=build_tool_registry(None),
        wm_snapshot={},
        l5_summary="",
        trigger_command="whoami",
        correlation_id=None,
    )
    assert rec is None
