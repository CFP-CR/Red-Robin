"""redrobin/api/app.py — FastAPI application factory with lifespan-managed state.

The lifespan handler is a thin orchestrator over the ``_start_*`` helpers
in :mod:`redrobin.api.lifespan_helpers`. Each helper owns a single
startup concern (event bus, rules loader, working memory + reducer,
engagement scope, verdict pipeline, CS adapters, lesson promoter, L5
summariser) and returns a close hook. The orchestrator calls them in
order at startup and tears them down in the original well-tested order
on shutdown.

Slice 8 (v0.1.x hardening) was a pure refactor: the body was split out
of this file without changing any resource creation or shutdown
sequencing. Slice 10 will reorder shutdown (drain background tasks
before adapter close); Slice 11 will touch the Sigma matcher.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, AsyncIterator, Coroutine

from fastapi import Depends, FastAPI, Request

from redrobin.api.adapters_router import router as adapters_router
from redrobin.api.auth import require_bearer
from redrobin.api.events_router import router as events_router
from redrobin.api.lifespan_helpers import (
    start_adapters,
    start_engagement,
    start_event_bus,
    start_l5_summarizer,
    start_lesson_promoter,
    start_pipeline,
    start_rules,
    start_working_memory,
)
from redrobin.api.tui_ws import router as tui_ws_router
from redrobin.api.verdicts_router import router as verdicts_router
from redrobin.application.config import Settings, validate_for_boot
from redrobin.application.startup_banner import format_banner

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    # Slice 3 (v0.1.x hardening): fail-fast on missing secrets unless the
    # operator opted into degraded mode. ``require_root_exists=False`` keeps
    # TestClient happy — pytest fixtures point REDROBIN_ROOT at a tmp_path
    # that may not yet exist when the lifespan runs.
    validate_for_boot(settings)

    # Phase 2 condition 4 — daemon-wide tracker for fire-and-forget asyncio
    # tasks. Two production callsites (verdicts_router PASS-dispatch and
    # verdict_pipeline ESCALATE Layer B) used to leak tasks on shutdown.
    # The tracker is initialised before any router or pipeline runs so
    # every callsite can rely on it via ``app.state.background_task_factory``.
    background_tasks: set[asyncio.Task[Any]] = set()
    app.state.background_tasks = background_tasks

    def _track_task(
        coro: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    app.state.background_task_factory = _track_task

    # Start each subsystem. Order matters — see lifespan_helpers for the
    # contract of each helper. The close hooks are kept by name (not a
    # list) so the shutdown order can stay bit-for-bit identical to the
    # pre-refactor monolithic body.
    _, close_bus = await start_event_bus(app, settings)
    _, stop_rules = await start_rules(app, settings)
    _, close_wm = await start_working_memory(app, settings)
    await start_engagement(app, settings)  # no close hook
    _, _close_pipeline = await start_pipeline(app, settings)  # no-op close
    _, close_adapters = await start_adapters(app, settings)
    _, close_promoter = await start_lesson_promoter(app, settings)
    _, close_summarizer = await start_l5_summarizer(app, settings)

    logger.info(
        "daemon lifespan startup complete: rules_dir=%s rules=%d adapters=%d "
        "promoter_interval=%.1fs l5_interval=%.1fs",
        settings.rules_dir,
        len(app.state.initial_rules),
        len(app.state.cs_adapters),
        settings.promoter_interval_sec,
        settings.l5_interval_sec,
    )

    # Slice 7 (v0.1.x hardening): structured startup banner. Surfaces the
    # resolved config (engagement, rules, adapters, secret key set/missing,
    # L5/promoter tuning) at boot so operators can diagnose "why is it doing
    # X" without diving into logs. Emitted to stderr so the daemon's stdout
    # remains available for structured JSON output. Secret VALUES never
    # appear — only the literal strings ``set`` / ``MISSING (degraded)``.
    try:
        _version = _pkg_version("redrobin")
    except PackageNotFoundError:
        _version = "0.0.0+unknown"
    _banner = format_banner(
        settings=settings,
        rules_count=len(app.state.rules_loader.list_rules()),
        engagement_name=(
            app.state.engagement.name if app.state.engagement is not None else None
        ),
        version=_version,
        adapter_names=list(app.state.cs_adapters.keys()),
    )
    print(_banner, file=sys.stderr, flush=True)

    try:
        yield
    finally:
        # Shutdown ordering preserved from the pre-refactor monolithic
        # body. Slice 10 will revisit (drain tasks before adapter close);
        # for this slice we keep it bit-for-bit identical.
        #
        # 0. Close adapter REST clients first so any in-flight dispatch
        #    sees a live event loop. Failures during shutdown are
        #    swallowed — we'd rather get to event_bus.close() cleanly
        #    than crash on a half-open httpx socket.
        await close_adapters()
        # 1. Drain tracked fire-and-forget tasks (verdicts_router PASS
        #    dispatch + pipeline ESCALATE Layer B). The 5s timeout balances
        #    "give an in-flight LLM call time to finish" against "don't hang
        #    a CTRL+C shutdown". Stragglers are cancelled. This happens
        #    BEFORE closing adapter clients in the previous loop — except we
        #    already closed them; the dispatch tasks are now operating on a
        #    closed httpx session and will fail fast in their own logging
        #    handler, which is fine.
        if background_tasks:
            try:
                done, pending = await asyncio.wait(
                    background_tasks, timeout=5.0
                )
                for task in pending:
                    task.cancel()
            except Exception:
                logger.exception("error draining background_tasks")
        # 2. Cancel the promoter + summariser loops. Both run
        #    independently of the bus and reducer; cancelling them before
        #    ``bus.close()`` avoids a tail promotion/summarisation racing
        #    the shutdown.
        await close_promoter()
        await close_summarizer()
        # 3. Close the bus — this broadcasts the sentinel to all subscribers
        #    including the reducer. The bus close is what signals reducer to exit
        #    after flushing any final episode write to the episodic DB.
        await close_bus()
        # 4. Wait for the reducer to drain and exit cleanly.
        await close_wm()
        await stop_rules()
        logger.info("daemon lifespan shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Red Robin Daemon", version="0.1.0", lifespan=lifespan)
    app.include_router(adapters_router)
    app.include_router(verdicts_router)
    app.include_router(events_router)
    app.include_router(tui_ws_router)

    @app.get("/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/protected", dependencies=[Depends(require_bearer)])
    async def protected() -> dict[str, str]:
        return {"status": "authenticated"}

    @app.get("/v1/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/adapter/heartbeat")
    async def adapter_heartbeat(adapter: str) -> dict[str, str]:
        """Simple liveness probe for the .cna heartbeat loop.

        Unauthenticated and stateless — the .cna fires this every 30s so the
        operator's CS event log warns "[Red Robin] daemon unreachable" the
        moment the daemon dies. Distinct from POST /v1/adapters/heartbeat
        which is session-bound and refreshes a live registry entry.
        """
        return {"status": "ok", "adapter": adapter}

    @app.get("/v1/readyz")
    async def readyz(request: Request) -> dict[str, str]:
        loader = getattr(request.app.state, "rules_loader", None)
        if loader is not None and loader.hot_reload_active:
            return {"status": "ready"}
        return {"status": "not_ready"}

    return app
