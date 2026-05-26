"""Layer B retroactive verdict must carry the real command, not '?'.

#141: `_run_layer_b` had `command` but never passed it to `_on_retroactive`, so the
opsec.verdict payload omitted it and the reducer defaulted to '?'
(working_memory_reducer.py:166), corrupting episodes/findings/promoted_lessons.
Mirror of test_verdict_pipeline_layer_b_beacon_id.py.
"""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline_factory import build_pipeline_for_test


@pytest.mark.asyncio
async def test_layer_b_retroactive_includes_command() -> None:
    bus = EventBus()
    queue = await bus.subscribe()
    pipeline = build_pipeline_for_test(event_bus=bus)

    await pipeline._on_retroactive(
        correlation_id="evt_abc123",
        beacon_id="bid_TESTBEACON",
        command="mimikatz logonpasswords",
        original_verdict="ESCALATE",
        new_verdict="BLOCK",
        reason="lsass credential extraction",
    )

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["type"] == "opsec.verdict"
    payload = event["payload"]
    assert payload["command"] == "mimikatz logonpasswords"
    assert payload["beacon_id"] == "bid_TESTBEACON"
    assert payload["verdict"] == "BLOCK"
