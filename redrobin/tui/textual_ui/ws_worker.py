"""WS worker for the Textual client: connect to /v1/tui and feed each frame
through the registry. Mirrors the legacy ws_client's connect/auth, but updates
reactive state instead of calling invalidate()."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import connect

from redrobin.tui.textual_ui.frames import Registry

logger = logging.getLogger(__name__)


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
) -> None:
    """Auto-reconnecting consumer. ``target`` is the RedRobinApp; ``registry``
    routes frames to handlers that set its reactive state."""
    full = build_ws_url(url, token)
    async for ws in connect(full, ping_interval=20, ping_timeout=20):
        try:
            async for raw in ws:
                if stop_event.is_set():
                    return
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                handle_raw(target=target, raw=raw, registry=registry)
        except Exception as exc:
            logger.warning("ws_worker reconnecting after error: %s", exc)
            if stop_event.is_set():
                return
            continue
        if stop_event.is_set():
            return


__all__ = ["build_ws_url", "handle_raw", "ws_loop"]
