"""redrobin/api/auth.py — bearer token dependency for FastAPI routes."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from redrobin.application.transport_auth import get_or_create_token


async def require_bearer(authorization: str = Header(...)) -> None:
    expected = f"Bearer {get_or_create_token()}"
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )
