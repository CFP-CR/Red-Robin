"""WS worker for the Textual client: connect to /v1/tui and feed each frame
through the registry. Mirrors the legacy ws_client's connect/auth, but updates
reactive state instead of calling invalidate()."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
from urllib.parse import urlencode

from websockets.asyncio.client import connect

from redrobin.tui.textual_ui.frames import Registry

logger = logging.getLogger(__name__)

# Inbound frame cap for the client socket. ``websockets`` defaults ``max_size`` to 1 MiB, while
# the daemon's connect frames are bounded by ITEM COUNT and never by bytes: ``chat_history``
# carries up to 50 whole turns verbatim (``tui_ws._HISTORY_LINES``), one of which can be an
# arbitrary operator paste — ``application/chat/history.py``'s ``_MAX_TURN_CHARS`` clips exactly
# that for the LLM slice and has no counterpart on this path — and a ``working_memory_snapshot``
# was measured at 900 KB during the #41 audit. Over the cap the library closes the connection
# with 1009 "message too big", ``ws_loop`` catches it and reconnects with NO backoff, the daemon
# re-sends the same frame on the new connect, and the two hot-loop while that frame NEVER renders.
# 16 MiB matches uvicorn's own ``ws_max_size`` default, i.e. the server end of this same socket.
# EXPLICIT rather than ``None``: an unbounded client would let a broken or hostile daemon OOM the
# TUI. Pinned by ``test_ws_loop_caps_inbound_frames_above_the_library_default``.
_MAX_FRAME_BYTES = 16 * 1024 * 1024


def build_ws_url(base: str, token: str | None) -> str:
    """Append ``?token=`` — the daemon authenticates /v1/tui via query param."""
    if not token:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({'token': token})}"


def handle_raw(*, target: Any, raw: str, registry: Registry) -> bool:
    """Decode one WS text frame and dispatch it. Never raises (a bad frame
    must not kill the worker loop). Returns True if a handler ran."""
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("ws_worker: dropped non-JSON frame")
        return False
    if not isinstance(frame, dict):
        return False
    return registry.dispatch(target=target, frame=frame)


async def ws_loop(
    *,
    target: Any,
    url: str,
    token: str | None,
    registry: Registry,
    stop_event: asyncio.Event,
    set_state: Callable[[str], None] | None = None,
) -> None:
    """Auto-reconnecting consumer. ``target`` is the RedRobinApp; ``registry``
    routes frames to handlers that set its reactive state. ``set_state`` (optional)
    receives ``"connecting"``/``"connected"`` as the socket transitions so the UI
    can show daemon-connection status."""
    full = build_ws_url(url, token)
    if set_state:
        set_state("connecting")
    async for ws in connect(full, ping_interval=20, ping_timeout=20, max_size=_MAX_FRAME_BYTES):
        if set_state:
            set_state("connected")
        setattr(target, "_ws_send", ws.send)  # the app sends operator messages via this
        try:
            async for raw in ws:
                if stop_event.is_set():
                    return
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                handle_raw(target=target, raw=raw, registry=registry)
        except Exception as exc:
            setattr(target, "_ws_send", None)
            logger.warning("ws_worker reconnecting after error: %s", exc)
            if set_state:
                set_state("connecting")
            if stop_event.is_set():
                return
            continue
        setattr(target, "_ws_send", None)
        if stop_event.is_set():
            return


__all__ = ["build_ws_url", "handle_raw", "ws_loop"]
