"""Layer B retroactive verdict must include beacon_id in payload.

Pre-existing inconsistency: Layer A's _publish_layer_a_event includes
beacon_id (verdict_pipeline.py:232), but _on_retroactive (Layer B path)
does not. The CS Aggressor adapter needs beacon_id to route the verdict
back to the right beacon console via blog($bid, ...).
"""
from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline import VerdictPipeline


@pytest.mark.asyncio
async def test_layer_b_publish_includes_beacon_id():
    bus = EventBus()
    queue = await bus.subscribe()

    # Instantiate the pipeline with the bus; layer_a / layer_b providers
    # are not exercised in this test — we call _on_retroactive directly.
    pipeline = VerdictPipeline.__new__(VerdictPipeline)  # type: ignore[call-arg]
    pipeline._bus = bus  # type: ignore[attr-defined]

    await pipeline._on_retroactive(
        correlation_id="evt_abc123",
        beacon_id="bid_TESTBEACON",
        original_verdict="ESCALATE",
        new_verdict="BLOCK",
        reason="Reflective DLL injection signature",
    )

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["type"] == "opsec.verdict"
    payload = event["payload"]
    assert payload["beacon_id"] == "bid_TESTBEACON"
    assert payload["correlation_id"] == "evt_abc123"
    assert payload["verdict"] == "BLOCK"
    assert payload["previous_verdict"] == "ESCALATE"
    assert payload["source"] == "layer_b"
