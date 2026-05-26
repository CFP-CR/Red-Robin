"""WebSocket endpoint /v1/adapters/events — pushes opsec.verdict events to
the CS Aggressor adapter.

Mirror of tui_ws.py with three differences:
  1. No initial working_memory_snapshot (adapter doesn't track WM).
  2. Server-side filter: only opsec.verdict events forwarded (YAGNI for
     v0.1 — manifest events_consumed filtering arrives in v0.2 when more
     event types are consumed).
  3. Dedicated endpoint keeps the adapter contract decoupled from the
     TUI protocol; both subscribe to the same EventBus internally.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from redrobin.application.transport_auth import get_or_create_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/v1/adapters/events")
async def adapter_events_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = None  # Defensive: ensure `queue` is defined before the
    # try/finally even if bus.subscribe() raises (#127).

    try:
        queue = await bus.subscribe()
        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            if event.get("type") == "opsec.verdict":
                await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("adapter events WS disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)
