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
from redrobin.domain.memory import WorkingMemory, working_memory_to_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/v1/tui")
async def tui_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    bus = websocket.app.state.event_bus
    queue = None  # Defensive: ensure `queue` is defined before the
    # try/finally even if bus.subscribe() raises (#127).

    try:
        queue = await bus.subscribe()
        snapshot_payload = await _build_initial_snapshot(websocket.app)
        await websocket.send_json({"type": "working_memory_snapshot", "payload": snapshot_payload})

        while True:
            event = await queue.get()
            if event.get("_type") == "_close":
                break
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)


async def _build_initial_snapshot(app) -> dict:
    """Build a complete working_memory_snapshot payload from the live repo.

    Uses ``working_memory_to_snapshot`` so the connect-time frame carries ALL
    13 WorkingMemory fields (#139). Tolerates missing repo / missing file by
    serializing an empty WorkingMemory, which matters because some tests
    instantiate ``create_app()`` without a working-memory repo.
    """
    repo = getattr(app.state, "working_memory_repo", None)
    engagement_id = getattr(app.state, "engagement_id", None)
    if repo is None or not repo.exists():
        return working_memory_to_snapshot(WorkingMemory(engagement_id=engagement_id or ""))
    return working_memory_to_snapshot(repo.load())
