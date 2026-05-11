"""redrobin/api/events_router.py — POST /v1/events ingestion.

Adapters POST envelopes here. Daemon validates schema + appends to per-engagement
events.jsonl (audit trail per spec §5.1).
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


@router.post("")
async def ingest(
    envelope: dict[str, Any],
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

    # Append to events.jsonl for the active engagement
    active = svc.current()
    if active is None:
        # Engagement not selected — events still validate but discarded
        return {"ok": True, "stored": False}

    engagement_path = REDROBIN_ROOT / "engagements" / active
    if not engagement_path.exists():
        return {"ok": True, "stored": False}

    events_jsonl = engagement_path / "events.jsonl"
    with events_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(envelope) + "\n")

    return {"ok": True, "stored": True}


@router.post("/replay", status_code=202)
async def replay_event(event: dict[str, Any], request: Request) -> dict[str, str]:
    """Forward an event onto the in-process event bus so all subscribers see it.

    Used by ``redrobin replay`` to feed an archived JSONL trace through the
    daemon as if it were live. No schema validation — the trace owner is
    responsible for ensuring it matches whatever consumers expect.
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event_bus not initialized",
        )
    await bus.publish(event)
    return {"status": "accepted"}
