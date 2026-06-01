"""redrobin/api/events_router.py — POST /v1/events ingestion.

Adapters POST canonical wire envelopes (see ``protocol/schemas/v0.1/``) here.
The daemon validates schema, appends to per-engagement ``events.jsonl`` (audit
trail per spec §5.1), and republishes the event onto the in-process EventBus
so the working-memory reducer + TUI WS subscribers see live wire events.

The bus convention is ``{"type": <wire_type>, "payload": <event_body>}`` so
both production wire envelopes and replayed bus-shape dicts converge on the
same shape before reaching subscribers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from redrobin.api.auth import require_bearer
from redrobin.application.dedup_keys import DedupKeys, derive_key
from redrobin.application.engagement_service import EngagementService
from redrobin.application.event_ingestion import (
    redrobin_root,
    validate_envelope,
    validate_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", dependencies=[Depends(require_bearer)])


def _engagement_service_dep() -> EngagementService:
    return EngagementService()


def _envelope_to_bus_event(envelope: dict[str, Any]) -> dict[str, Any]:
    """Translate a wire envelope into the ``{type, payload}`` bus shape.

    The bus subscribers (reducer, TUI WS) expect ``{type, payload}``; the
    canonical wire shape is ``{spec_version, id, ts, source, event: {type, ...}}``.
    We lift ``event.type`` to the top level and pass the rest of ``event``
    (still tagged with ``type`` so subscribers that want it can read it) as
    ``payload``.
    """
    event_body = dict(envelope["event"])
    return {"type": event_body["type"], "payload": event_body}


def _is_envelope(data: dict[str, Any]) -> bool:
    """A wire envelope has ``spec_version`` + ``event``; a bus dict doesn't."""
    return "spec_version" in data and "event" in data


@router.post("")
async def ingest(
    envelope: dict[str, Any],
    request: Request,
    svc: EngagementService = Depends(_engagement_service_dep),
) -> dict[str, bool]:
    # Validate envelope shape
    errors = validate_envelope(envelope)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"envelope_errors": errors},
        )
    # Validate event payload against per-type schema
    event_type = envelope["event"]["type"]
    event_body = envelope["event"]
    payload_errors = validate_event(event_type, event_body)
    if payload_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"event_errors": payload_errors},
        )

    # Dedup gate (B3): drop re-emissions BEFORE append + bus publish.
    # ``derive_key`` returns ``None`` for events we choose not to dedup (no
    # correlation_id, missing bid, unknown type) — those pass through. On
    # gate-hit we return early with ``duplicate: True`` so the producer sees
    # an explicit signal; on miss we fall through to the existing side effects
    # AND the response body stays byte-identical to the pre-B3 shape
    # (``{"ok": True, "stored": <bool>}``) so exact-equality tests upstream
    # remain green.
    dedup_keys: DedupKeys | None = getattr(request.app.state, "dedup_keys", None)
    if dedup_keys is not None:
        key = derive_key(event_body)
        if key is not None and not await dedup_keys.check_and_insert(key):
            logger.debug("dedup hit: %s", key)
            return {"ok": True, "stored": False, "duplicate": True}

    # Append to events.jsonl for the active engagement.
    active = svc.current()
    stored = False
    if active is not None:
        engagement_path = redrobin_root() / "engagements" / active
        if engagement_path.exists():
            events_jsonl = engagement_path / "events.jsonl"
            with events_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(envelope) + "\n")
            stored = True

    # Republish onto the bus so the reducer + TUI WS see the wire event.
    # This is the critical link that turns real CS adapter POSTs into live
    # working-memory updates and operator-visible event-stream frames.
    bus = getattr(request.app.state, "event_bus", None)
    if bus is not None:
        await bus.publish(_envelope_to_bus_event(envelope))

    return {"ok": True, "stored": stored}


@router.post("/replay")
async def replay_event(event: dict[str, Any], request: Request) -> JSONResponse:
    """Forward a JSONL trace line onto the in-process event bus.

    Accepts either a full wire envelope (the shape ``events.jsonl`` actually
    stores) or a pre-flattened bus-shape dict (older traces, hand-crafted
    fixtures). Envelopes are translated to bus shape so subscribers see the
    same ``{type, payload}`` dicts regardless of how the trace was captured.

    Used by ``redrobin replay`` to feed an archived JSONL trace through the
    daemon as if it were live. Schema validation is intentionally skipped —
    the operator owns the trace contents.

    Dedup gate (B3): runs at the top of the handler, BEFORE the bus publish.
    Status code is dynamic — 202 on miss (forwarded to bus), 200 on hit
    (silently dropped as a duplicate). The miss-path body is byte-identical
    to the pre-B3 shape (``{"status": "accepted"}``) so exact-equality tests
    upstream remain green.
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event_bus not initialized",
        )

    # Normalize to (event_body, bus_event) — same translation as ingest, so
    # the dedup key derives off the event payload regardless of whether the
    # caller sent an envelope or a flat bus dict.
    if _is_envelope(event):
        event_body = event["event"]
        bus_event = _envelope_to_bus_event(event)
    else:
        event_body = event
        bus_event = event

    dedup_keys: DedupKeys | None = getattr(request.app.state, "dedup_keys", None)
    if dedup_keys is not None:
        key = derive_key(event_body)
        if key is not None and not await dedup_keys.check_and_insert(key):
            logger.debug("dedup hit (replay): %s", key)
            return JSONResponse(status_code=200, content={"status": "duplicate"})

    await bus.publish(bus_event)
    return JSONResponse(status_code=202, content={"status": "accepted"})
