"""redrobin/api/app.py — FastAPI application factory with lifespan-managed state.

The lifespan handler boots the rules loader at startup, primes the in-memory
rule set, and starts the watchdog hot-reload observer. Shutdown stops the
observer (closes runtime gap #7 — hot reload was implemented but never invoked
at daemon boot).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request

from redrobin.api.adapters_router import router as adapters_router
from redrobin.api.auth import require_bearer
from redrobin.api.events_router import router as events_router
from redrobin.api.tui_ws import router as tui_ws_router
from redrobin.api.verdicts_router import router as verdicts_router
from redrobin.application.engagement_loader import Engagement, EngagementLoader
from redrobin.application.event_bus import EventBus
from redrobin.application.rules_lifecycle import RulesLoader
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.verdict_pipeline_factory import (
    build_default_dependencies,
    build_pipeline,
)
from redrobin.application.working_memory_lifecycle import WorkingMemory, WorkingMemoryRepo
from redrobin.application.working_memory_reducer import WorkingMemoryReducer

logger = logging.getLogger(__name__)


def _default_rules_dir() -> Path:
    env = os.environ.get("REDROBIN_RULES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".redrobin" / "rules"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    rules_dir = _default_rules_dir()
    if "REDROBIN_RULES_DIR" not in os.environ:
        logger.warning(
            "REDROBIN_RULES_DIR not set; defaulting to %s — set explicitly in tests/CI to avoid home pollution.",
            rules_dir,
        )
    rules_dir.mkdir(parents=True, exist_ok=True)

    rules_loader = RulesLoader(rule_dir=rules_dir)
    initial_rules = rules_loader.load_all()
    app.state.rules_loader = rules_loader

    event_bus = EventBus()
    app.state.event_bus = event_bus

    # Working memory persistence
    wm_path = Path(os.environ.get(
        "REDROBIN_WORKING_MEMORY_PATH",
        str(Path.home() / ".redrobin" / "working_memory.json"),
    ))
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    working_memory_repo = WorkingMemoryRepo(wm_path)
    app.state.working_memory_repo = working_memory_repo

    engagement_id = os.environ.get("REDROBIN_ENGAGEMENT_ID", "eng-default")
    app.state.engagement_id = engagement_id

    # Slice 6 — engagement-aware scope. Read engagement.yaml from
    # ``~/.redrobin/engagements/<slug>/`` (root configurable for tests) and
    # build the pipeline's ScopeEnforcer from it. Missing slug falls back to
    # wildcard scope so the dev workflow without a configured engagement
    # still works.
    engagements_dir = Path(
        os.environ.get(
            "REDROBIN_ENGAGEMENTS_DIR",
            str(Path.home() / ".redrobin" / "engagements"),
        )
    )
    engagement_slug = os.environ.get("REDROBIN_ENGAGEMENT_SLUG")
    engagement: Engagement | None = None
    if engagement_slug:
        try:
            engagement = EngagementLoader(engagements_dir).load(engagement_slug)
        except FileNotFoundError:
            logger.warning(
                "engagement slug %s set but file not found under %s — "
                "falling back to wildcard scope",
                engagement_slug,
                engagements_dir,
            )
            engagement = None

    if engagement is None:
        scope = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
        app.state.engagement = None
    else:
        scope = ScopeEnforcer(
            in_scope=engagement.in_scope, out_of_scope=engagement.out_of_scope
        )
        app.state.engagement = engagement
        # Keep app.state.engagement_id consistent with the loaded slug so the
        # working-memory reducer + downstream consumers see a single ID.
        app.state.engagement_id = engagement.slug
        engagement_id = engagement.slug

    # Seed an empty working memory file if none exists so the reducer's
    # synchronous load → mutate → save cycle has something to read on the
    # first event.
    if not working_memory_repo.exists():
        working_memory_repo.save(WorkingMemory(engagement_id=engagement_id))

    # Reducer subscribes to the bus and mutates working memory.
    reducer = WorkingMemoryReducer(
        bus=event_bus, repo=working_memory_repo, engagement_id=engagement_id
    )
    started = asyncio.Event()
    reducer.on_started = started.set  # type: ignore[attr-defined]
    reducer_task = asyncio.create_task(reducer.run(), name="working_memory_reducer")
    await started.wait()  # ensure subscription is active before yielding
    app.state.reducer_task = reducer_task

    # Verdict pipeline — built once at startup with the bus wired so Layer A
    # verdicts publish to the same bus the reducer + TUI WS subscribe to.
    # Reuses the already-loaded rules to avoid double-reading the rules dir.
    # Slice 6: ``scope`` is built from engagement.yaml above (or wildcard
    # fallback). The factory still ships a default scope on ``deps.scope`` —
    # we override it here with the engagement-aware enforcer.
    deps = build_default_dependencies(rules=initial_rules)
    verdict_pipeline = build_pipeline(
        event_bus=event_bus,
        scope=scope,
        layer_a=deps.layer_a,
        layer_b=deps.layer_b,
    )
    app.state.verdict_pipeline = verdict_pipeline

    def _on_reload(rules: list) -> None:
        logger.debug("rules_loader: reloaded %d rules", len(rules))
        # Hot-reload Layer A: swap the rule set on the live engine so the
        # singleton pipeline picks up edits without a daemon restart.
        deps.layer_a.update_rules(rules)

    rules_loader.start_hot_reload(_on_reload)
    logger.info(
        "daemon lifespan startup complete: rules_dir=%s rules=%d",
        rules_dir,
        len(initial_rules),
    )

    try:
        yield
    finally:
        # 1. Close the bus first — this broadcasts the sentinel to all subscribers
        #    including the reducer. The bus close is what signals reducer to exit.
        await event_bus.close()
        # 2. Wait for the reducer to drain and exit cleanly.
        try:
            await asyncio.wait_for(reducer_task, timeout=2.0)
        except asyncio.TimeoutError:
            reducer_task.cancel()
        rules_loader.stop_hot_reload()
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

    @app.get("/v1/readyz")
    async def readyz(request: Request) -> dict[str, str]:
        loader = getattr(request.app.state, "rules_loader", None)
        if loader is not None and loader.hot_reload_active:
            return {"status": "ready"}
        return {"status": "not_ready"}

    return app
