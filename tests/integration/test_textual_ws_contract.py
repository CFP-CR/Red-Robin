"""The daemon emits exactly the frame types the Textual client registry knows.
Reuses the daemon TestClient pattern from tests/e2e/test_retroactive_journey.py."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.tui.textual_ui.app import REGISTRY  # registers wm_snapshot + opsec.verdict


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
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


def _recv_type(ws, wanted: str):
    while True:
        frame = ws.receive_json()
        if frame.get("type") == wanted:
            return frame


def test_connect_snapshot_type_is_handled(daemon):
    app, client = daemon
    token = _token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"
        assert REGISTRY.has_handler(first["type"]) is True


def test_published_verdict_type_is_handled(daemon):
    app, client = daemon
    token = _token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # drain connect snapshot
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "c1",
                    "verdict": "BLOCK",
                    "command": "mimikatz",
                    "source": "layer_a",
                },
            },
        )
        frame = _recv_type(ws, "opsec.verdict")
        assert REGISTRY.has_handler(frame["type"]) is True
        # And handle_raw routes it without raising.
        from redrobin.tui.textual_ui import ws_worker

        class _T:
            snapshot = {}
            events = []

        t = _T()
        assert ws_worker.handle_raw(target=t, raw=json.dumps(frame), registry=REGISTRY) is True
        assert t.events[-1]["verdict"] == "BLOCK"
