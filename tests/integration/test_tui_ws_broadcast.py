"""Slice 3 integration: TUI WebSocket receives bus events + initial snapshot."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.infrastructure.transport.bearer import get_or_create_token


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_ws_sends_initial_snapshot(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"
        assert "payload" in first


def test_ws_forwards_published_event(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # discard snapshot
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "opsec.verdict", "payload": {"verdict": "ALLOW"}},
        )
        evt = ws.receive_json()
        assert evt["type"] == "opsec.verdict"
        assert evt["payload"]["verdict"] == "ALLOW"


def test_two_ws_clients_both_receive(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws_a, \
            client.websocket_connect(f"/v1/tui?token={token}") as ws_b:
        ws_a.receive_json()  # snapshot
        ws_b.receive_json()  # snapshot
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "opsec.verdict", "payload": {"verdict": "BLOCK"}},
        )
        ea = ws_a.receive_json()
        eb = ws_b.receive_json()
        assert ea["type"] == "opsec.verdict"
        assert eb["type"] == "opsec.verdict"
        assert ea == eb
