"""redrobin/api/app.py — FastAPI application factory with lifespan-managed state.

The lifespan handler boots the rules loader at startup, primes the in-memory
rule set, and starts the watchdog hot-reload observer. Shutdown stops the
observer (closes runtime gap #7 — hot reload was implemented but never invoked
at daemon boot).
"""

from __future__ import annotations

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
from redrobin.application.event_bus import EventBus
from redrobin.application.rules_lifecycle import RulesLoader

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

    def _on_reload(rules: list) -> None:
        logger.debug("rules_loader: reloaded %d rules", len(rules))

    rules_loader.start_hot_reload(_on_reload)
    logger.info(
        "daemon lifespan startup complete: rules_dir=%s rules=%d",
        rules_dir,
        len(initial_rules),
    )

    try:
        yield
    finally:
        await event_bus.close()
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
