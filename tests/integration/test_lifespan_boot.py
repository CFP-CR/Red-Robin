"""Daemon lifespan boot + the readiness contract.

``/v1/readyz`` reports whether the lifespan startup sequence completed. It used
to report whether the Sigma rule-reload watchdog was alive — a pre-flight-gate
concept masquerading as a health check, which died with the gate. The contract
below is the replacement: ready iff every ``start_*`` helper has returned, and
not_ready before boot finishes or once teardown begins.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from redrobin.api.app import create_app


@pytest.fixture(autouse=True)
def _allow_degraded_boot(monkeypatch):
    """Boot validation exits(2) without API keys.

    Every test here boots the lifespan and none of them assert on the LLM
    surface, so opt into degraded explicitly. Without this the module only
    passed when an earlier test had leaked keys into ``os.environ`` (issue
    #51) — which is never true in CI, where no ``.env`` exists.
    """
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")


@pytest.mark.asyncio
async def test_lifespan_initializes_app_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            assert app.state.event_bus is not None
            assert app.state.working_memory_repo is not None
            assert app.state.ready is True
            resp = await client.get("/v1/healthz")
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_readyz_reports_ready_inside_lifespan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            resp = await client.get("/v1/readyz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readyz_reports_not_ready_outside_lifespan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No lifespan_context — startup never ran, so the flag was never set.
        resp = await client.get("/v1/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_readyz_reports_not_ready_after_shutdown(tmp_path: Path, monkeypatch):
    """Readiness must drop the moment teardown starts, not linger until process exit."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            assert (await client.get("/v1/readyz")).json()["status"] == "ready"
        resp = await client.get("/v1/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_ready"
