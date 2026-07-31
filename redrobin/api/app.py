"""redrobin/api/app.py — FastAPI application factory with lifespan-managed state.

The lifespan handler is a thin orchestrator over the ``_start_*`` helpers
in :mod:`redrobin.api.lifespan_helpers`. Each helper owns a single
startup concern (event bus, engagement, dedup keys, working memory +
reducer, enricher, assistant, L5 summariser) and returns a close hook.
The orchestrator calls them in order at startup and tears them down in
the original well-tested order on shutdown.

Slice 8 (v0.1.x hardening) was a pure refactor: the body was split out
of this file without changing any resource creation or shutdown
sequencing.
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
    start_assistant,
    start_dedup,
    start_engagement,
    start_enricher,
    start_event_bus,
    start_l5_summarizer,
    start_working_memory,
)
from redrobin.api.tui_ws import router as tui_ws_router
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
    # tasks, which used to leak on shutdown. The tracker is initialised before
    # any router runs so every callsite can rely on it via
    # ``app.state.background_task_factory``.
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
    # contract of each helper. Close hooks are kept by name (not a list)
    # so the shutdown sequence in the ``finally`` block is explicit and
    # each phase can be reordered independently — see the ``finally``
    # block below for the current contract.
    _, close_bus = await start_event_bus(app, settings)
    await start_engagement(
        app, settings
    )  # no close hook — must run before working_memory so the reducer captures the resolved engagement_id
    _, close_dedup = await start_dedup(
        app, settings
    )  # must run AFTER start_engagement; reads app.state.engagement to pick the per-engagement keys file
    _, close_wm = await start_working_memory(app, settings)
    _, close_enricher = await start_enricher(app, settings)
    _, close_assistant = await start_assistant(app, settings)
    _, close_summarizer = await start_l5_summarizer(app, settings)

    logger.info(
        "daemon lifespan startup complete: l5_interval=%.1fs",
        settings.l5_interval_sec,
    )

    # Slice 7 (v0.1.x hardening): structured startup banner. Surfaces the
    # resolved config (engagement, secret key set/missing, L5 tuning) at
    # boot so operators can diagnose "why is it doing X" without diving
    # into logs. Emitted to stderr so the daemon's stdout remains available
    # for structured JSON output. Secret VALUES never appear — only the
    # literal strings ``set`` / ``MISSING (degraded)``.
    try:
        _version = _pkg_version("redrobin")
    except PackageNotFoundError:
        _version = "0.0.0+unknown"
    _banner = format_banner(
        settings=settings,
        engagement_name=(app.state.engagement.name if app.state.engagement is not None else None),
        version=_version,
        scope_empty=getattr(app.state, "scope_empty", False),
    )
    print(_banner, file=sys.stderr, flush=True)

    # Readiness. Every ``start_*`` helper above has returned, so the daemon is
    # fully wired: bus, engagement, working memory + reducer, enricher,
    # assistant, summariser. This flag IS the definition of "ready" —
    # it replaces the pre-flight gate's definition (the Sigma hot-reload
    # watchdog being alive), which died with the gate.
    app.state.ready = True

    try:
        yield
    finally:
        # Stop reporting ready the moment teardown begins — the close hooks
        # below dismantle the very subsystems readiness asserts are up.
        app.state.ready = False
        # Graceful, best-effort shutdown: each close hook is wrapped
        # individually so one bad close doesn't prevent the rest from
        # running.
        #
        # 1. Drain tracked fire-and-forget tasks. The 5s timeout balances
        #    "give an in-flight LLM call time to finish" against "don't hang
        #    a CTRL+C shutdown". Stragglers are cancelled and then awaited
        #    briefly so their CancelledError handlers can run before the
        #    event loop tears down.
        if background_tasks:
            try:
                pending_initial = [t for t in background_tasks if not t.done()]
                if pending_initial:
                    _, still_pending = await asyncio.wait(pending_initial, timeout=5.0)
                    for task in still_pending:
                        task.cancel()
                    if still_pending:
                        await asyncio.gather(*still_pending, return_exceptions=True)
            except Exception:
                logger.exception("error draining background_tasks")
        # 2. Cancel the summariser loop. It runs independently of the bus and
        #    reducer; cancelling it before ``bus.close()`` avoids a tail
        #    summarisation racing the shutdown.
        try:
            await close_summarizer()
        except Exception:
            logger.exception("close_summarizer failed")
        # 3. Close the bus — this broadcasts the sentinel to all subscribers
        #    including the reducer. The bus close is what signals reducer to exit
        #    after flushing any final episode write to the episodic DB.
        try:
            await close_bus()
        except Exception:
            logger.exception("close_bus failed")
        # 4. Wait for the reducer to drain and exit cleanly.
        try:
            await close_wm()
        except Exception:
            logger.exception("close_wm failed")
        # Wait for the assistant to drain after the bus sentinel. It started
        # AFTER the enricher, so in reverse-of-startup order it closes first.
        try:
            await close_assistant()
        except Exception:
            logger.exception("close_assistant failed")
        # Wait for the enricher to drain after the bus sentinel (like the reducer).
        try:
            await close_enricher()
        except Exception:
            logger.exception("close_enricher failed")
        # No-op close for dedup keys (in-memory + already-durable disk state);
        # kept in the close list to match the helper convention so a future
        # real closer slots in here without re-wiring the lifespan.
        try:
            await close_dedup()
        except Exception:
            logger.exception("close_dedup failed")
        logger.info("daemon lifespan shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Red Robin Daemon", version="0.1.0", lifespan=lifespan)
    app.include_router(adapters_router)
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
        """Readiness probe: has the lifespan startup sequence completed?

        ``app.state.ready`` is set at the end of lifespan startup and cleared
        the moment shutdown begins, so this reports ``not_ready`` both before
        boot finishes and during teardown. ``getattr`` covers the app-built-
        but-lifespan-never-entered case, where the attribute is absent.
        """
        if getattr(request.app.state, "ready", False):
            return {"status": "ready"}
        return {"status": "not_ready"}

    return app
