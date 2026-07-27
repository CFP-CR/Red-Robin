"""Slice 2 integration: in-process pub/sub bus with bounded queue fan-out."""

from __future__ import annotations

import asyncio

import pytest

from redrobin.application.event_bus import EventBus


@pytest.mark.asyncio
async def test_two_subscribers_each_receive_event():
    bus = EventBus()
    sub1 = await bus.subscribe()
    sub2 = await bus.subscribe()

    await bus.publish({"type": "x.test", "payload": {"n": 1}})

    e1 = await asyncio.wait_for(sub1.get(), timeout=0.5)
    e2 = await asyncio.wait_for(sub2.get(), timeout=0.5)
    assert e1["type"] == "x.test"
    assert e2["type"] == "x.test"
    assert e1 == e2


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    sub = await bus.subscribe()
    await bus.unsubscribe(sub)
    await bus.publish({"type": "x.test"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_queue_full_drops_oldest():
    bus = EventBus(queue_maxsize=2)
    sub = await bus.subscribe()
    await bus.publish({"type": "a", "n": 1})
    await bus.publish({"type": "a", "n": 2})
    await bus.publish({"type": "a", "n": 3})  # forces drop of oldest

    e1 = await sub.get()
    e2 = await sub.get()
    assert (e1["n"], e2["n"]) == (2, 3)


@pytest.mark.asyncio
async def test_close_unblocks_subscribers():
    bus = EventBus()
    sub = await bus.subscribe()
    close_task = asyncio.create_task(bus.close())
    sentinel = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert sentinel == {"_type": "_close"}
    await close_task


@pytest.mark.asyncio
async def test_app_state_has_event_bus(tmp_path, monkeypatch):
    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.event_bus, EventBus)
        sub = await app.state.event_bus.subscribe()
        await app.state.event_bus.publish({"type": "boot.test"})
        e = await asyncio.wait_for(sub.get(), timeout=0.5)
        assert e["type"] == "boot.test"
