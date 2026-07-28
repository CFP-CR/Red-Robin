"""Slice 3 integration: TUI WebSocket receives bus events + initial snapshot."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.infrastructure.transport.bearer import get_or_create_token
from tests.integration._ws_helpers import receive_json_bounded


@pytest.fixture()
def app_client():
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_ws_sends_initial_snapshot(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = receive_json_bounded(ws)
        assert first["type"] == "working_memory_snapshot"
        assert "payload" in first


def test_ws_forwards_published_event(app_client):
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        # two connect frames now: the snapshot and the chat history. BOUNDED, because
        # these skips run BEFORE the publish below — if a connect frame ever regresses
        # away, an unbounded receive here could never be satisfied and would hang the
        # session instead of failing this test.
        receive_json_bounded(ws)
        receive_json_bounded(ws)
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "working_memory_snapshot", "payload": {"current_phase": "recon"}},
        )
        # The post-publish read is the one that MUST be bounded: a connect-frame read can be
        # satisfied by whatever frame does arrive, but if bus forwarding regresses then no
        # frame can ever arrive here, so an unbounded receive hangs forever.
        evt = receive_json_bounded(ws)
        assert evt["type"] == "working_memory_snapshot"
        assert evt["payload"]["current_phase"] == "recon"


def test_two_ws_clients_both_receive(app_client):
    app, client = app_client
    token = get_or_create_token()
    with (
        client.websocket_connect(f"/v1/tui?token={token}") as ws_a,
        client.websocket_connect(f"/v1/tui?token={token}") as ws_b,
    ):
        # two connect frames each now: the snapshot and the chat history. Bounded for the
        # same reason as above — these skips precede the publish.
        receive_json_bounded(ws_a)
        receive_json_bounded(ws_a)
        receive_json_bounded(ws_b)
        receive_json_bounded(ws_b)
        client.portal.call(
            app.state.event_bus.publish,
            {"type": "working_memory_snapshot", "payload": {"current_phase": "lateral"}},
        )
        # Bounded for the same reason as the single-client test: a bus-forwarding regression
        # leaves nothing to receive, and nothing in this repo would end the run.
        ea = receive_json_bounded(ws_a)
        eb = receive_json_bounded(ws_b)
        assert ea["type"] == "working_memory_snapshot"
        assert eb["type"] == "working_memory_snapshot"
        assert ea == eb


def test_ws_initial_snapshot_carries_all_fields(app_client):
    """#139: the connect-time snapshot must include the fields that used to be
    dropped (scope/objectives/kill_chain/etc.), not just the 5
    collections."""
    app, client = app_client
    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = receive_json_bounded(ws)
        payload = first["payload"]
        for key in (
            "client",
            "scope_in_scope",
            "scope_out_of_scope",
            "objectives",
            "current_phase",
            "kill_chain_status",
            "rules_of_engagement",
            "defenses_observed",
            "tools_used",
            "hosts",
            "credentials",
            "findings",
            "sessions",
        ):
            assert key in payload, f"snapshot missing {key}"
