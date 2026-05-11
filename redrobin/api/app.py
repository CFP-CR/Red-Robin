"""redrobin/api/app.py — FastAPI application factory."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from redrobin.api.adapters_router import router as adapters_router
from redrobin.api.auth import require_bearer
from redrobin.api.verdicts_router import router as verdicts_router


def create_app() -> FastAPI:
    app = FastAPI(title="Red Robin Daemon", version="0.1.0")
    app.include_router(adapters_router)
    app.include_router(verdicts_router)

    @app.get("/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/protected", dependencies=[Depends(require_bearer)])
    async def protected() -> dict[str, str]:
        return {"status": "authenticated"}

    return app
