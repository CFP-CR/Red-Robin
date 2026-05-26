"""redrobin/api/verdicts_router.py — Path 1 quick verdict endpoint.

Per spec §1.2.1: alias bodies POST here with a hard timeout of ~150ms.
Returns PASS/WARN/BLOCK/ESCALATE. On ESCALATE the daemon schedules Layer B
async (fire-and-forget); retroactive verdicts surface via TUI WebSocket (Task 32).

Slice 5 — the pipeline is built once in the lifespan and stored on
``app.state.verdict_pipeline``. The route reads it from the request rather
than rebuilding per-request, so the bus wiring (Layer A emission + retroactive
hook) is shared with the rest of the daemon.

Slice 9 — on PASS, when the request carries an ``adapter`` field naming a
loaded CS adapter, the daemon POSTs the command to that team server's
``/api/v1/beacons/{bid}/consoleCommand`` endpoint. The dispatch is
fire-and-forget; failures are logged but never propagated as 5xx — the
operator's verdict response is the source of truth, the CS POST is a side
effect of an already-approved command.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from redrobin.api.auth import require_bearer
from redrobin.application.event_bus import EventBus
from redrobin.application.verdict_pipeline import VerdictPipeline
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)
from redrobin.domain.opsec import VerdictAction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/verdict", dependencies=[Depends(require_bearer)])


class QuickVerdictRequest(BaseModel):
    bid: str
    command: str
    raw: str
    source_event_id: str = ""
    # Slice 9 — when set, the daemon dispatches the command to the named
    # CS adapter on PASS. Defaults to None so the TUI's direct submission
    # path (no team-server side effect required) keeps working unchanged.
    adapter: str | None = None
    # Slice 11 — Windows process metadata threads through to Layer A so
    # extended Sigma rules can constrain on integrity level / image / user /
    # cwd / description. All optional; the .cna only emits ``integrity_level``
    # in v0.1.x (derived from binfo "user" trailing " *"). Distinguishing
    # Medium/Low/System and surfacing OriginalFileName/CurrentDirectory etc.
    # requires a beacon-side BOF and is deferred to v0.2.
    integrity_level: str | None = None
    original_filename: str | None = None
    user: str | None = None
    current_directory: str | None = None
    description: str | None = None


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


async def _dispatch_to_cs_adapter(
    request: Request, adapter_name: str, bid: str, command: str
) -> None:
    """Fire-and-forget dispatch to the named CS adapter's REST API.

    Errors are logged and swallowed — the verdict response has already
    travelled back to the alias body, so a failed dispatch must not become
    a 500. An unknown adapter name is a misconfiguration, not a runtime
    failure, so it's logged at warning level rather than error.
    """
    adapters = getattr(request.app.state, "cs_adapters", {}) or {}
    client = adapters.get(adapter_name)
    if client is None:
        logger.warning(
            "verdict dispatch requested for unknown adapter %r (loaded: %s)",
            adapter_name,
            sorted(adapters.keys()),
        )
        return
    try:
        await client.dispatch_console_command(bid, command)
    except Exception:
        logger.exception(
            "cs_adapter dispatch failed: adapter=%s bid=%s command=%s",
            adapter_name,
            bid,
            command,
        )


@router.post("/quick", response_model=QuickVerdictResponse)
async def quick(
    req: QuickVerdictRequest,
    request: Request,
    pipeline: VerdictPipeline = Depends(_pipeline_dep),
) -> QuickVerdictResponse:
    verdict = await pipeline.quick_verdict(
        command=req.command,
        raw=req.raw,
        source_event_id=req.source_event_id,
        beacon_id=req.bid,
        integrity_level=req.integrity_level,
        original_filename=req.original_filename,
        user=req.user,
        current_directory=req.current_directory,
        description=req.description,
    )

    # Slice 9 — only PASS verdicts dispatch. WARN/BLOCK/ESCALATE never reach
    # the team server from this path; WARN is operator-soft-warned and the
    # caller (alias body) decides whether to send. We fire-and-forget so the
    # HTTP response is not blocked on the CS side-channel.
    #
    # Phase 2 condition 4: use the lifespan's tracked task factory so the
    # dispatch coroutine is registered on ``app.state.background_tasks`` and
    # the shutdown finalizer awaits / cancels it instead of leaking.
    if verdict.action == VerdictAction.PASS and req.adapter and req.bid:
        factory = getattr(request.app.state, "background_task_factory", asyncio.create_task)
        factory(_dispatch_to_cs_adapter(request, req.adapter, req.bid, req.command))

    return QuickVerdictResponse(
        action=verdict.action.value,
        reason=verdict.reason,
        rule_id=verdict.rule_id,
        suggestions=verdict.suggestions,
    )
