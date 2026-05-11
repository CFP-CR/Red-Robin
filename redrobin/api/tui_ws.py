"""redrobin/api/tui_ws.py — WebSocket bridge for TUI bidirectional events.

The TUI subscribes via WebSocket and receives:
  - working_memory_snapshot  (initial + on change)
  - event_stream_append      (each new event)
  - chat_response            (Layer B answer)
  - retroactive_verdict      (Layer B late verdict)

The TUI sends:
  - operator_question        (chat input)
  - slash_command            (:rr ...)
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from redrobin.application.transport_auth import get_or_create_token

router = APIRouter()


@router.websocket("/v1/tui")
async def tui_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    if token != get_or_create_token():
        await websocket.close(code=1008, reason="invalid token")
        return
    await websocket.accept()
    try:
        # Send initial snapshot stub (full wiring lands in Task 39 alongside CLI integration)
        await websocket.send_json({"type": "working_memory_snapshot", "data": {}})
        # Echo loop — placeholder until full event router wires in Task 39
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "invalid JSON"})
                continue
            await websocket.send_json({"type": "ack", "received": payload})
    except WebSocketDisconnect:
        return
