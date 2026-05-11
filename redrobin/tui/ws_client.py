"""WebSocket client that streams daemon events into TUIState.

Connects to ``/v1/tui`` and translates each frame into a TUIState mutation:

- ``working_memory_snapshot`` replaces the cached snapshot.
- ``opsec.verdict`` with ``source=layer_b`` updates an earlier verdict in place
  (the Layer B retroactive correction overwrites the Layer A verdict that shares
  the same ``correlation_id``).
- All other events are appended to the bounded event deque.

The asyncio loop reconnects forever using ``websockets``' async iterator helper;
``stop_event`` lets the host TUI cancel cleanly on shutdown.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from websockets.asyncio.client import connect

from redrobin.tui.state import TUIState

logger = logging.getLogger(__name__)


def handle_message(*, raw: str, state: TUIState, invalidate: Callable[[], None]) -> None:
    """Decode one WS frame, mutate state, request a paint.

    Pure: no I/O. Designed to be unit-tested without spinning up an event loop.
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ws_client: dropped non-JSON message")
        return

    etype = msg.get("type")
    payload = msg.get("payload") or {}

    if etype == "working_memory_snapshot":
        state.replace_snapshot(payload)
    elif etype == "opsec.verdict":
        corr = payload.get("correlation_id")
        source = payload.get("source", "layer_a")
        if source == "layer_b" and corr is not None:
            # Retroactive correction — mutate the earlier layer_a entry in place
            # so the operator's event log shows one verdict line per command, not
            # two. update_verdict() also appends if the original was evicted from
            # the deque, so we never silently drop a correction.
            state.update_verdict(corr, payload)
        else:
            state.append_event(msg)
    else:
        state.append_event(msg)

    invalidate()


async def run_client(
    *,
    url: str,
    token: str | None,
    state: TUIState,
    invalidate: Callable[[], None],
    stop_event: asyncio.Event,
) -> None:
    """Auto-reconnecting WS consumer.

    The daemon's ``/v1/tui`` endpoint actually authenticates via the ``?token=``
    query parameter (see ``redrobin/api/tui_ws.py``). We still send an
    ``Authorization`` header for forward compatibility — the daemon ignores it
    today and other deployments may validate it. The caller (``run_tui``) is
    responsible for appending ``?token=`` to ``url``.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async for ws in connect(url, additional_headers=headers, ping_interval=20, ping_timeout=20):
        try:
            async for raw in ws:
                if stop_event.is_set():
                    return
                # websockets gives bytes for binary frames and str for text;
                # we expect JSON text frames from the daemon.
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                handle_message(raw=raw, state=state, invalidate=invalidate)
        except Exception as exc:
            logger.warning("ws_client reconnecting after error: %s", exc)
            if stop_event.is_set():
                return
            continue
        if stop_event.is_set():
            return
