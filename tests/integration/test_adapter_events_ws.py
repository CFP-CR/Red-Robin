"""Integration tests for /v1/adapters/events WebSocket endpoint.

Mirrors test_tui_ws_broadcast.py: lifespan-bound TestClient + portal-call
to publish from the sync test thread onto the daemon's async event loop.

Cases:
  - Auth via ?token=<x> query parameter (same as TUI)
  - Server-side filter: only opsec.verdict events forwarded
  - Non-verdict events do NOT arrive (filter is selective, not blocking)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from redrobin.api.app import create_app
from redrobin.infrastructure.transport.bearer import get_or_create_token


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_adapter_events_ws_rejects_invalid_token(app_client):
    _app, client = app_client
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/v1/adapters/events?token=wrong"):
            pass
    assert excinfo.value.code == 1008, (
        f"expected close code 1008 (policy violation / invalid token), got {excinfo.value.code}"
    )


def test_adapter_events_ws_forwards_opsec_verdict(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/adapters/events?token={token}") as ws:
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "beacon_id": "bid_X",
                    "verdict": "BLOCK",
                    "reason": "test",
                    "source": "layer_b",
                    "correlation_id": "c_1",
                    "previous_verdict": "ESCALATE",
                },
            },
        )
        frame = ws.receive_json()
        assert frame["type"] == "opsec.verdict"
        assert frame["payload"]["beacon_id"] == "bid_X"
        assert frame["payload"]["source"] == "layer_b"


def test_adapter_events_ws_filters_non_verdict_events(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/adapters/events?token={token}") as ws:
        # First publish a non-verdict event — must NOT arrive
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "host.observation", "payload": {"hostname": "irrelevant"}},
        )
        # Then a verdict — MUST arrive (proves filter is selective, not blocking)
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "beacon_id": "bid_Y",
                    "verdict": "WARN",
                    "reason": "test",
                    "source": "layer_b",
                    "correlation_id": "c_2",
                    "previous_verdict": "ESCALATE",
                },
            },
        )
        # If filter is broken, the first frame would be host.observation
        frame = ws.receive_json()
        assert frame["type"] == "opsec.verdict"
        assert frame["payload"]["beacon_id"] == "bid_Y"
