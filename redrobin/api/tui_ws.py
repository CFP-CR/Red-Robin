"""WebSocket endpoint /v1/tui — pushes EventBus events to the TUI client.

On connect the client receives a ``working_memory_snapshot`` frame and a
``chat_history`` frame, then every event published on ``app.state.event_bus`` is
forwarded over the socket. The per-client queue is unsubscribed on disconnect (or
sentinel ``{_type: _close}``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from redrobin.application.transport_auth import get_or_create_token
from redrobin.domain.memory import WorkingMemory, working_memory_to_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()

# Only these operator-originated types may be injected onto the bus from the
# TUI. Everything else inbound is dropped (the TUI cannot forge engagement,
# opsec, or working-memory events).
_TUI_ALLOWED = {"operator.question", "operator.answer"}

# How many transcript LINES the chat pane hydrates from on connect. Named for lines, not
# turns, because ``tail`` caps lines and drops unparseable ones without backfilling, so a
# transcript with a malformed line yields fewer. A module constant, not a Setting (YAGNI,
# matching MAX_TAIL_LINES) — one bounded read, never a full read sliced afterwards.
_HISTORY_LINES = 50


async def _send(websocket: WebSocket, event: dict) -> bool:
    """Push one event to the TUI. Returns False when the client is gone so the caller
    stops the outbound loop cleanly. A disconnect — or a concurrent close that surfaces
    as uvicorn's ASGI 'websocket.send after close' RuntimeError — must NEVER crash the
    ASGI handler. A single unserializable event is logged and skipped (returns True) so
    one bad frame can't tear the whole stream."""
    try:
        await websocket.send_json(event)
        return True
    except (WebSocketDisconnect, RuntimeError) as exc:
        # Client gone (disconnect, or the ASGI 'send after close' RuntimeError). A
        # breadcrumb so an *unexpected* RuntimeError leaves a trace instead of vanishing.
        logger.debug("tui_ws: outbound send stopped, client gone: %r", exc)
        return False
    except Exception:
        logger.exception("tui_ws: dropped an unserializable outbound event")
        return True


async def _publish_operator_message(bus, msg) -> None:
    """Publish an operator-originated TUI message on the bus, if it is an allowlisted
    operator.* type, stamped source='operator' (so the assistant consumes it and the
    TUI does not re-render its own echo). Never raises (a bad frame is dropped)."""
    try:
        if not isinstance(msg, dict):
            return
        etype = msg.get("type")
        if etype not in _TUI_ALLOWED:
            return
        payload = {k: v for k, v in msg.items() if k != "type"}
        payload["source"] = "operator"
        await bus.publish({"type": etype, "payload": payload})
    except Exception:
        logger.exception("tui_ws: failed to publish operator message")


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
        if not await _send(
            websocket, {"type": "working_memory_snapshot", "payload": snapshot_payload}
        ):
            return  # client already gone — the outer finally unsubscribes

        # Chat hydration (#41). Sent AFTER the snapshot and BEFORE the live loop, so the
        # pane holds the thread before any new event lands on it.
        #
        # We subscribe to the bus above and only read the transcript here. Measured on
        # this code today, that window contains no guaranteed yield point (the bus lock is
        # uncontended and send_json did not suspend even on a 900 KB frame), so a turn
        # cannot currently slip in between and render twice. The order is kept anyway,
        # deliberately: if a yield point ever appears here, this way costs a visible,
        # harmless duplicate, whereas reading before subscribing would SILENTLY LOSE the
        # turn. Do not reverse it.
        #
        # Precisely: this is the better of two lossy orders, not a lossless one. ``ChatRecorder``
        # is an INDEPENDENT bus consumer, so its append is not synchronous with the publish.
        # Read-then-subscribe loses ALWAYS whatever is published in that window — absent from the
        # file at read time, and no subscription yet to carry it live. Subscribe-then-read loses
        # only the narrower case of a turn published just BEFORE the subscribe whose append the
        # recorder has not flushed by the time we read here; the recorder still writes it, so the
        # next connect hydrates it.
        if not await _send(
            websocket,
            {"type": "chat_history", "payload": await _build_chat_history(websocket.app)},
        ):
            return  # client already gone — the outer finally unsubscribes

        async def _receive() -> None:
            # Concurrent inbound path: publish allowlisted operator.* messages the
            # TUI sends. Runs as a background task so the outbound loop below keeps
            # the original single-await, teardown-stable shape.
            while True:
                try:
                    msg = await websocket.receive_json()
                except WebSocketDisconnect:
                    return  # clean disconnect — end the receive side
                except Exception:
                    logger.warning("tui_ws: dropped a bad inbound frame")
                    continue  # a malformed frame must NOT kill the socket (never-crash)
                await _publish_operator_message(bus, msg)

        recv_task = asyncio.create_task(_receive())
        # Retrieve the task's outcome once it finishes so nothing is ever left
        # unretrieved. _receive catches everything (it only ever returns or is
        # cancelled), so this is purely defensive.
        recv_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

        try:
            # Outbound loop. Two suspension points now: `queue.get` (as before) and
            # the `send_json` inside `_send`. A disconnect while an event is already
            # queued lands the loop at `send_json` on a closed socket, which surfaces
            # as WebSocketDisconnect or uvicorn's ASGI 'send after close' RuntimeError;
            # `_send` absorbs both and returns False so we stop cleanly instead of
            # crashing the ASGI handler. Concurrency lives entirely in the background
            # _receive task, which is cancelled *synchronously* (no await) on the way
            # out so it can never orphan onto a tearing-down loop nor trip anyio's
            # re-delivered-cancellation accounting (an awaited teardown would).
            while True:
                event = await queue.get()
                if event.get("_type") == "_close":
                    break
                if not await _send(websocket, event):
                    break  # client gone mid-send — stop cleanly (finally cancels _receive)
        finally:
            recv_task.cancel()
    except WebSocketDisconnect:
        logger.info("TUI WebSocket client disconnected")
    finally:
        if queue is not None:
            await bus.unsubscribe(queue)


async def _build_initial_snapshot(app) -> dict:
    """Build a complete working_memory_snapshot payload from the live repo.

    Uses ``working_memory_to_snapshot`` so the connect-time frame carries ALL
    WorkingMemory fields (#139), not just the 5 collections. Tolerates missing
    repo / missing file by serializing an empty WorkingMemory, which matters
    because some tests instantiate ``create_app()`` without a working-memory repo.
    """
    repo = getattr(app.state, "working_memory_repo", None)
    engagement_id = getattr(app.state, "engagement_id", None)
    if repo is None or not repo.exists():
        return working_memory_to_snapshot(WorkingMemory(engagement_id=engagement_id or ""))
    return working_memory_to_snapshot(repo.load())


async def _build_chat_history(app) -> dict:
    """Build the connect-time ``chat_history`` payload from the transcript repo.

    Mirrors ``_build_initial_snapshot``: reads the repo the lifespan left on
    ``app.state``, so this layer never imports infrastructure (import-linter contract 4).

    The repo is ABSENT whenever the assistant is disabled — ``lifespan_helpers`` gates it
    on ``assistant_enabled``, which is off by default — so the empty result is the common
    path, not a defensive edge. Both the read AND the mapping sit inside the guard: the
    endpoint's outer try catches only WebSocketDisconnect, so anything raised here would
    close the socket and drop the TUI into its reconnect loop. A chat that cannot hydrate
    must never cost the operator their Working Memory and Sessions panes.

    EVERY reconnect re-runs this read and re-sends up to ``_HISTORY_LINES`` turns that the pane
    then DISCARDS — hydration is one-shot per pane (``chat._on_chat_history``). Accepted: nothing
    tells the daemon whether the pane is already hydrated (the inbound path carries only
    ``_TUI_ALLOWED`` operator.* messages), and a ``ts`` watermark was rejected for the reasons in
    the spec's Reconnect section. The cost is real, not zero: synchronous file I/O on the connect
    path, and the same text crosses the socket again — including credentials the operator may have
    pasted into a turn (``recorder.py`` never LOGS a turn's text for exactly that reason).
    """
    repo = getattr(app.state, "chat_history_repo", None)
    if repo is None:
        return {"turns": []}
    try:
        turns = repo.tail(max_lines=_HISTORY_LINES)
        return {"turns": [asdict(turn) for turn in turns]}
    except Exception:
        logger.exception("tui_ws: could not read the chat transcript; hydrating an empty thread")
        return {"turns": []}
