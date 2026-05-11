"""WebSocket endpoint /v1/tui — pushes EventBus events to the TUI client.

On connect the client receives a single ``working_memory_snapshot`` event
(empty shape until Slice 4 wires the reducer), then every event published
on ``app.state.event_bus`` is forwarded over the socket. The per-client
queue is unsubscribed on disconnect (or sentinel ``{_type: _close}``).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from redrobin.application.transport_auth import get_or_create_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/v1/tui")
async def tui_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = await bus.subscribe()

    try:
        snapshot_payload = await _build_initial_snapshot(websocket.app)
        await websocket.send_json(
            {"type": "working_memory_snapshot", "payload": snapshot_payload}
        )

        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        await bus.unsubscribe(queue)


async def _build_initial_snapshot(app) -> dict:
    """Build a working_memory_snapshot payload.

    Until Slice 4 wires the reducer, this returns an empty but valid shape.
    Slice 4 replaces the body with a real query of WorkingMemoryRepo.
    """
    return {
        "engagement_id": getattr(app.state, "engagement_id", None),
        "hosts": [],
        "credentials": [],
        "findings": [],
        "sessions": [],
    }
