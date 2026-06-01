"""Slice 1 integration: daemon lifespan boots rules loader with hot reload."""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_hot_reload_picks_up_new_rule(tmp_path: Path, monkeypatch):
    rules_dir = tmp_path / "rules" / "shared" / "sigma"
    rules_dir.mkdir(parents=True)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))

    app = create_app()
    async with app.router.lifespan_context(app):
        loader = app.state.rules_loader
        initial = len(loader.list_rules())

        new_rule = rules_dir / "test_mimikatz.yaml"
        new_rule.write_text(
            "title: Test Mimikatz Detection\n"
            "id: 00000000-0000-0000-0000-000000000001\n"
            "logsource: {product: windows}\n"
            "detection: {selection: {Image: mimikatz.exe}, condition: selection}\n"
            "level: high\n"
        )

        # watchdog needs a tick to pick up the change
        for _ in range(50):
            await asyncio.sleep(0.1)
            if len(loader.list_rules()) > initial:
                break

        assert len(loader.list_rules()) == initial + 1
