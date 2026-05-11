"""redrobin/api/verdicts_router.py — Path 1 quick verdict endpoint.

Per spec §1.2.1: alias bodies POST here with a hard timeout of ~150ms.
Returns PASS/WARN/BLOCK/ESCALATE. On ESCALATE the daemon schedules Layer B
async (fire-and-forget); retroactive verdicts surface via TUI WebSocket (Task 32).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from redrobin.api.auth import require_bearer
from redrobin.application.verdict_pipeline import VerdictPipeline
from redrobin.application.verdict_pipeline_factory import get_verdict_pipeline

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


def _pipeline_dep() -> VerdictPipeline:
    return get_verdict_pipeline()


@router.post("/quick", response_model=QuickVerdictResponse)
async def quick(
    req: QuickVerdictRequest,
    pipeline: VerdictPipeline = Depends(_pipeline_dep),
) -> QuickVerdictResponse:
    verdict = await pipeline.quick_verdict(
        command=req.command,
        raw=req.raw,
        source_event_id=req.source_event_id,
    )
    return QuickVerdictResponse(
        action=verdict.action.value,
        reason=verdict.reason,
        rule_id=verdict.rule_id,
        suggestions=verdict.suggestions,
    )
