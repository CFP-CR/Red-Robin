"""The daemon emits exactly the frame types the Textual client registry knows.
Reuses the daemon TestClient pattern from tests/e2e/test_retroactive_journey.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.tui.textual_ui.app import REGISTRY  # registers working_memory_snapshot + chat frames


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def test_connect_snapshot_type_is_handled(daemon):
    app, client = daemon
    token = _token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"
        assert REGISTRY.has_handler(first["type"]) is True
