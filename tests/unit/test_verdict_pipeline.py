"""Verdict pipeline — 4-stage two-tier protocol (spec §1.2.1)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.verdict_pipeline import VerdictPipeline
from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.domain.rules import Rule, RuleMatch, RuleSeverity


@pytest.fixture
def layer_a_empty():
    return LayerAEngine(rules=[], passthrough={"pwd", "cd", "ls"})


@pytest.fixture
def layer_a_with_mimikatz():
    rule = Rule(
        id="mimikatz_logonpasswords",
        match=RuleMatch(command="mimikatz", arg_pattern="logonpasswords"),
        severity=RuleSeverity.DANGER,
        reason="lsass touch",
    )
    return LayerAEngine(rules=[rule], passthrough=set())


@pytest.fixture
def scope():
    # Canonical engagement.yaml semantics: bare domain → suffix match.
    # ``acme.local`` matches ``dc01.acme.local`` via the spec's domain-suffix
    # rule; the legacy ``*.acme.local`` glob is no longer accepted by check().
    return ScopeEnforcer(in_scope=["acme.local"], out_of_scope=["10.0.99.0/24"])


@pytest.fixture
def layer_b_mock():
    lb = AsyncMock()
    lb.evaluate = AsyncMock(
        return_value=Verdict(
            action=VerdictAction.WARN, reason="late retroactive warn", rule_id=None
        )
    )
    return lb


@pytest.mark.asyncio
async def test_pipeline_passthrough_short_circuits(layer_a_empty, scope, layer_b_mock):
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_empty, layer_b=layer_b_mock)
    v = await pipeline.quick_verdict(command="pwd", raw="pwd")
    assert v.action == VerdictAction.PASS
    # Allow background tasks to settle
    await asyncio.sleep(0.01)
    layer_b_mock.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_scope_violation_blocks(layer_a_empty, scope, layer_b_mock):
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_empty, layer_b=layer_b_mock)
    v = await pipeline.quick_verdict(command="jump", raw="jump psexec64 10.0.99.5 listener")
    assert v.action == VerdictAction.BLOCK
    assert "ROE violation" in v.reason
    assert v.rule_id == "roe"
    await asyncio.sleep(0.01)
    layer_b_mock.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_scope_in_scope_pass_through(layer_a_empty, scope, layer_b_mock):
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_empty, layer_b=layer_b_mock)
    # No matching rule → ESCALATE, but in-scope target shouldn't block at Stage 1
    v = await pipeline.quick_verdict(command="jump", raw="jump psexec64 dc01.acme.local listener")
    # Stage 2 (Layer A empty) returns ESCALATE since "jump" not in passthrough
    assert v.action == VerdictAction.ESCALATE


@pytest.mark.asyncio
async def test_pipeline_layer_a_block_short_circuits(layer_a_with_mimikatz, scope, layer_b_mock):
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_with_mimikatz, layer_b=layer_b_mock)
    v = await pipeline.quick_verdict(command="mimikatz", raw="mimikatz sekurlsa::logonpasswords")
    assert v.action == VerdictAction.BLOCK
    assert v.rule_id == "mimikatz_logonpasswords"
    await asyncio.sleep(0.01)
    layer_b_mock.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_escalate_returns_immediately_and_schedules_layer_b(
    layer_a_empty, scope, layer_b_mock
):
    retroactive_received: list[Verdict] = []

    async def on_retroactive(v: Verdict) -> None:
        retroactive_received.append(v)

    pipeline = VerdictPipeline(
        scope=scope,
        layer_a=layer_a_empty,
        layer_b=layer_b_mock,
        on_retroactive_verdict=on_retroactive,
    )
    v = await pipeline.quick_verdict(command="unknown_cmd", raw="unknown_cmd args")
    # quick_verdict returns ESCALATE immediately
    assert v.action == VerdictAction.ESCALATE
    # Wait briefly for the async Layer B task to complete
    await asyncio.sleep(0.05)
    layer_b_mock.evaluate.assert_called_once()
    # Retroactive verdict callback fired (WARN per fixture)
    assert len(retroactive_received) == 1
    assert retroactive_received[0].action == VerdictAction.WARN


@pytest.mark.asyncio
async def test_pipeline_retroactive_callback_skipped_when_pass(scope, layer_a_empty):
    """If Layer B returns PASS retroactively, no callback fires (avoid noise)."""
    lb = AsyncMock()
    lb.evaluate = AsyncMock(
        return_value=Verdict(action=VerdictAction.PASS, reason="ok", rule_id=None)
    )
    callbacks: list[Verdict] = []

    async def on_retroactive(v: Verdict) -> None:
        callbacks.append(v)

    pipeline = VerdictPipeline(
        scope=scope,
        layer_a=layer_a_empty,
        layer_b=lb,
        on_retroactive_verdict=on_retroactive,
    )
    await pipeline.quick_verdict(command="unknown_cmd", raw="unknown_cmd")
    await asyncio.sleep(0.05)
    lb.evaluate.assert_called_once()
    assert callbacks == []  # PASS retroactive → no callback


@pytest.mark.asyncio
async def test_pipeline_layer_b_exception_does_not_crash(scope, layer_a_empty):
    """Layer B exception (e.g., LLM timeout) is logged, not propagated."""
    lb = AsyncMock()
    lb.evaluate = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_empty, layer_b=lb)
    # ESCALATE-returning command should still complete cleanly
    v = await pipeline.quick_verdict(command="weird", raw="weird args")
    assert v.action == VerdictAction.ESCALATE
    await asyncio.sleep(0.05)
    lb.evaluate.assert_called_once()
    # No exception bubbled up


@pytest.mark.asyncio
async def test_pipeline_strips_command_prefix_for_layer_a(scope, layer_a_with_mimikatz):
    """Layer A sees args without the leading command name."""
    lb = AsyncMock()
    pipeline = VerdictPipeline(scope=scope, layer_a=layer_a_with_mimikatz, layer_b=lb)
    v = await pipeline.quick_verdict(
        command="mimikatz", raw="mimikatz sekurlsa::logonpasswords privilege::debug"
    )
    # Layer A's regex was `arg_pattern="logonpasswords"` — should still match
    # against the args ("sekurlsa::logonpasswords privilege::debug").
    assert v.action == VerdictAction.BLOCK
