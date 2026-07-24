from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect

from redrobin.api.tui_ws import _send


class _WS:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc
        self.sent: list[dict] = []

    async def send_json(self, event: dict) -> None:
        if self._exc is not None:
            raise self._exc
        self.sent.append(event)


@pytest.mark.asyncio
async def test_send_returns_true_and_forwards_on_success():
    ws = _WS()
    assert await _send(ws, {"type": "x"}) is True
    assert ws.sent == [{"type": "x"}]


@pytest.mark.asyncio
async def test_send_returns_false_on_websocket_disconnect():
    assert await _send(_WS(WebSocketDisconnect()), {"type": "x"}) is False


@pytest.mark.asyncio
async def test_send_returns_false_on_asgi_closed_runtimeerror():
    # the exact live failure: send after the socket was concurrently closed
    exc = RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'")
    assert await _send(_WS(exc), {"type": "x"}) is False


@pytest.mark.asyncio
async def test_send_skips_unserializable_event_without_raising():
    # a single bad event must be logged + skipped (True = keep the stream alive), never crash
    assert await _send(_WS(TypeError("not serializable")), {"bad": object()}) is True
