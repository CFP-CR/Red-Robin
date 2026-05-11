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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from redrobin.api.auth import require_bearer
from redrobin.application.engagement_service import EngagementService
from redrobin.application.event_ingestion import (
    REDROBIN_ROOT,
    validate_envelope,
    validate_event,
)

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
    payload_errors = validate_event(event_type, envelope["event"])
    if payload_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"event_errors": payload_errors},
        )

    # Append to events.jsonl for the active engagement.
    active = svc.current()
    stored = False
    if active is not None:
        engagement_path = REDROBIN_ROOT / "engagements" / active
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


@router.post("/replay", status_code=202)
async def replay_event(event: dict[str, Any], request: Request) -> dict[str, str]:
    """Forward a JSONL trace line onto the in-process event bus.

    Accepts either a full wire envelope (the shape ``events.jsonl`` actually
    stores) or a pre-flattened bus-shape dict (older traces, hand-crafted
    fixtures). Envelopes are translated to bus shape so subscribers see the
    same ``{type, payload}`` dicts regardless of how the trace was captured.

    Used by ``redrobin replay`` to feed an archived JSONL trace through the
    daemon as if it were live. Schema validation is intentionally skipped —
    the operator owns the trace contents.
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event_bus not initialized",
        )
    if _is_envelope(event):
        bus_event = _envelope_to_bus_event(event)
    else:
        bus_event = event
    await bus.publish(bus_event)
    return {"status": "accepted"}
