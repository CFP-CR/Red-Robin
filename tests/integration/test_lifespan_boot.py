"""Slice 1 integration: daemon lifespan boots rules loader with hot reload."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from redrobin.api.app import create_app


@pytest.mark.asyncio
async def test_lifespan_initializes_app_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            assert app.state.rules_loader is not None
            assert app.state.rules_loader.hot_reload_active is True
            resp = await client.get("/v1/healthz")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_readyz_reports_ready_inside_lifespan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/v1/readyz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readyz_reports_not_ready_outside_lifespan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No lifespan_context — rules_loader was never set.
        resp = await client.get("/v1/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_ready"
