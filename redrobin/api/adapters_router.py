"""redrobin/api/adapters_router.py — adapter session lifecycle endpoints.

Per spec §4 Componente 8:
  POST /v1/adapters/connect       — handshake, returns adapter_session_id
  POST /v1/adapters/heartbeat     — keep-alive (every 10s per adapter manifest)
  POST /v1/adapters/disconnect    — graceful shutdown
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from redrobin.api.auth import require_bearer
from redrobin.application.adapter_sessions import (
    AdapterSessionRegistry,
    get_registry,
)

router = APIRouter(prefix="/v1/adapters", dependencies=[Depends(require_bearer)])


class ConnectRequest(BaseModel):
    manifest: dict[str, Any]


class ConnectResponse(BaseModel):
    adapter_session_id: str


class SessionRequest(BaseModel):
    adapter_session_id: str


class OkResponse(BaseModel):
    ok: bool


def _registry_dep() -> AdapterSessionRegistry:
    return get_registry()


@router.post("/connect", response_model=ConnectResponse)
async def connect(
    req: ConnectRequest,
    registry: AdapterSessionRegistry = Depends(_registry_dep),
) -> ConnectResponse:
    if "manifest_version" not in req.manifest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="manifest missing manifest_version",
        )
    if "name" not in req.manifest or "version" not in req.manifest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="manifest missing required name/version",
        )
    session = registry.connect(req.manifest)
    return ConnectResponse(adapter_session_id=session.session_id)


@router.post("/heartbeat", response_model=OkResponse)
async def heartbeat(
    req: SessionRequest,
    registry: AdapterSessionRegistry = Depends(_registry_dep),
) -> OkResponse:
    session = registry.heartbeat(req.adapter_session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return OkResponse(ok=True)


@router.post("/disconnect", response_model=OkResponse)
async def disconnect(
    req: SessionRequest,
    registry: AdapterSessionRegistry = Depends(_registry_dep),
) -> OkResponse:
    registry.disconnect(req.adapter_session_id)
    return OkResponse(ok=True)
