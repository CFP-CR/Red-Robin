"""Slice 5 integration: VerdictPipeline emits opsec.verdict events.

Closes Phase 2 gaps #3 (retroactive callback wired as None) and #9 (no producer
for ``opsec.verdict`` events). Layer A verdicts and Layer B retroactive verdicts
both publish on the EventBus with a ``source`` discriminator so the TUI can
distinguish the original verdict line (``source=layer_a``) from the retroactive
correction (``source=layer_b``, carries ``previous_verdict``).
"""
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline_factory import build_pipeline_for_test


@pytest.mark.asyncio
async def test_layer_a_verdict_emits_event() -> None:
    bus = EventBus()
    sub = await bus.subscribe()
    pipeline = build_pipeline_for_test(event_bus=bus)

    verdict = await pipeline.quick_verdict(
        command="mimikatz",
        raw="mimikatz sekurlsa::logonpasswords",
        source_event_id="corr-001",
    )
    # Phase 1 Verdict carries an `action` enum, not a `verdict` string.
    assert verdict.action.value in {"PASS", "WARN", "BLOCK", "ESCALATE"}

    evt = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert evt["type"] == "opsec.verdict"
    payload = evt["payload"]
    assert payload["correlation_id"] == "corr-001"
    # Reducer dispatches on payload["verdict"] string == "BLOCK"; keep the same
    # string action shape so the reducer can react without extra mapping.
    assert payload["verdict"] == verdict.action.value
    assert payload["source"] == "layer_a"


@pytest.mark.asyncio
async def test_retroactive_callback_emits_event() -> None:
    bus = EventBus()
    sub = await bus.subscribe()
    pipeline = build_pipeline_for_test(event_bus=bus)

    await pipeline._on_retroactive(
        correlation_id="corr-002",
        beacon_id="bid_TEST",
        original_verdict="PASS",
        new_verdict="BLOCK",
        reason="LLM flagged downstream impact",
    )
    evt = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert evt["type"] == "opsec.verdict"
    payload = evt["payload"]
    assert payload["correlation_id"] == "corr-002"
    assert payload["source"] == "layer_b"
    assert payload["verdict"] == "BLOCK"
    assert payload["previous_verdict"] == "PASS"
    assert payload["reason"] == "LLM flagged downstream impact"


@pytest.mark.asyncio
async def test_layer_a_emission_carries_beacon_and_command() -> None:
    """Reducer keys findings off beacon_id + command; both must reach the bus."""
    bus = EventBus()
    sub = await bus.subscribe()
    pipeline = build_pipeline_for_test(event_bus=bus)

    await pipeline.quick_verdict(
        command="ls",
        raw="ls",
        source_event_id="corr-003",
        beacon_id="B-1",
    )
    evt = await asyncio.wait_for(sub.get(), timeout=0.5)
    payload = evt["payload"]
    assert payload["beacon_id"] == "B-1"
    assert payload["command"] == "ls"
    # Passthrough commands publish too — TUI shows every gate decision.
    assert payload["verdict"] == "PASS"
