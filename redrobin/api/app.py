"""redrobin/api/app.py — FastAPI application factory.

Sub-routers are registered as they come online (adapters, verdicts, events, tui_ws).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from redrobin.api.auth import require_bearer


def create_app() -> FastAPI:
    app = FastAPI(title="Red Robin Daemon", version="0.1.0")

    @app.get("/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/protected", dependencies=[Depends(require_bearer)])
    async def protected() -> dict[str, str]:
        return {"status": "authenticated"}

    return app
