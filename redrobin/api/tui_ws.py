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
    queue = None  # Defensive: ensure `queue` is defined before the
                  # try/finally even if bus.subscribe() raises (#127).

    try:
        queue = await bus.subscribe()
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
        if queue is not None:
            await bus.unsubscribe(queue)


async def _build_initial_snapshot(app) -> dict:
    """Build a working_memory_snapshot payload from the live repo.

    Tolerates missing repo / missing file by returning an empty shape, which
    matters because some tests instantiate ``create_app()`` without setting
    ``REDROBIN_WORKING_MEMORY_PATH``.
    """
    repo = getattr(app.state, "working_memory_repo", None)
    engagement_id = getattr(app.state, "engagement_id", None)
    if repo is None or not repo.exists():
        return {
            "engagement_id": engagement_id,
            "hosts": [],
            "credentials": [],
            "findings": [],
            "sessions": [],
        }
    wm = repo.load()
    return {
        "engagement_id": engagement_id,
        "hosts": [_serialize(h) for h in wm.hosts],
        "credentials": [_serialize(c) for c in wm.credentials],
        "findings": [_serialize(f) for f in wm.findings],
        "sessions": [_serialize(s) for s in wm.sessions],
    }


def _serialize(obj):
    """Render a domain dataclass/struct as a JSON-safe dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return obj
