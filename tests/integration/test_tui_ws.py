"""TUI WebSocket bridge — auth + echo."""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("redrobin.infrastructure.filesystem.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.verdict_pipeline_factory.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.event_ingestion.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.engagement_service.REDROBIN_ROOT", root)
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()
    yield root


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


def test_ws_rejects_wrong_token(client):
    try:
        with client.websocket_connect("/v1/tui?token=wrong"):
            pass
    except Exception:
        # Expected: connection rejected
        return
    pytest.fail("expected websocket to be rejected")


def test_ws_accepts_correct_token_sends_initial_snapshot(client, _isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "working_memory_snapshot"


def test_ws_echoes_json_messages(client, _isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # discard initial snapshot
        ws.send_json({"type": "operator_question", "text": "hello"})
        ack = ws.receive_json()
        assert ack["type"] == "ack"
        assert ack["received"]["text"] == "hello"


def test_ws_returns_error_on_invalid_json(client, _isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    token = get_or_create_token()
    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        ws.receive_json()  # discard initial snapshot
        ws.send_text("{not valid json")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "invalid JSON" in err["message"]
