import pytest

from redrobin.application.agents.assistant.agent import (
    apply_deltas_to_snapshot,
    build_push_context,
    build_system_prompt,
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
