"""Slice 14 integration: heartbeat, VectorStore JSON, doctor exit code."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    """Same isolation pattern as test_http_auth — points REDROBIN_ROOT at tmp."""
    monkeypatch.setattr(
        "redrobin.infrastructure.filesystem.REDROBIN_ROOT", tmp_path / ".redrobin"
    )
    (tmp_path / ".redrobin").mkdir(parents=True, exist_ok=True)
    yield tmp_path / ".redrobin"


def test_heartbeat_endpoint(tmp_path, monkeypatch, _isolated_root):
    """GET /v1/adapter/heartbeat returns ok + echoes the adapter name.

    This is the simple liveness probe the .cna fires every 30s — distinct
    from the session-bound POST /v1/adapters/heartbeat used during a live
    handshake. The .cna can hit this before/without a connect() handshake
    so the operator sees "daemon unreachable" warnings the moment the
    daemon goes away.
    """
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/v1/adapter/heartbeat?adapter=cs-main")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["adapter"] == "cs-main"
