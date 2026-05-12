"""TUI WebSocket bridge — auth + initial snapshot.

The echo loop tests were removed in Slice 3: the endpoint no longer echoes
operator input. Bus-driven broadcasting is covered by
``test_tui_ws_broadcast.py``.
"""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    yield root


@pytest.fixture
def client(_isolated_root):
    # Use the ``with`` context so FastAPI's lifespan runs and app.state.event_bus
    # is populated — Slice 3 onwards the WS endpoint reads it on accept.
    with TestClient(create_app()) as client:
        yield client


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
