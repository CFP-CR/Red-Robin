"""redrobin/api/verdicts_router.py — Path 1 quick verdict endpoint.

Per spec §1.2.1: alias bodies POST here with a hard timeout of ~150ms.
Returns PASS/WARN/BLOCK/ESCALATE. On ESCALATE the daemon schedules Layer B
async (fire-and-forget); retroactive verdicts surface via TUI WebSocket (Task 32).

Slice 5 — the pipeline is built once in the lifespan and stored on
``app.state.verdict_pipeline``. The route reads it from the request rather
than rebuilding per-request, so the bus wiring (Layer A emission + retroactive
hook) is shared with the rest of the daemon.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from redrobin.api.auth import require_bearer
from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline import VerdictPipeline
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)

router = APIRouter(prefix="/v1/verdict", dependencies=[Depends(require_bearer)])


class QuickVerdictRequest(BaseModel):
    bid: str
    command: str
    raw: str
    source_event_id: str = ""


class QuickVerdictResponse(BaseModel):
    action: str
    reason: str
    rule_id: str | None
    suggestions: list[str]


def _pipeline_dep(request: Request) -> VerdictPipeline:
    pipeline = getattr(request.app.state, "verdict_pipeline", None)
    if pipeline is not None:
        return pipeline  # type: ignore[no-any-return]
    # Lifespan didn't run (TestClient without context manager). Build a
    # lazy fallback pipeline so the legacy tests that construct
    # ``TestClient(create_app())`` without entering the lifespan still work.
    # The fallback gets a fresh EventBus stored alongside it on app.state so
    # subsequent requests in the same test reuse the same pipeline. The bus
    # has no subscribers in this path, so emission is a no-op fan-out.
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        bus = EventBus()
        request.app.state.event_bus = bus
    deps = build_default_dependencies()
    pipeline = build_pipeline(
        event_bus=bus,
        scope=deps.scope,
        layer_a=deps.layer_a,
        layer_b=deps.layer_b,
    )
    request.app.state.verdict_pipeline = pipeline
    return pipeline


@router.post("/quick", response_model=QuickVerdictResponse)
async def quick(
    req: QuickVerdictRequest,
    pipeline: VerdictPipeline = Depends(_pipeline_dep),
) -> QuickVerdictResponse:
    verdict = await pipeline.quick_verdict(
        command=req.command,
        raw=req.raw,
        source_event_id=req.source_event_id,
        beacon_id=req.bid,
    )
    return QuickVerdictResponse(
        action=verdict.action.value,
        reason=verdict.reason,
        rule_id=verdict.rule_id,
        suggestions=verdict.suggestions,
    )
