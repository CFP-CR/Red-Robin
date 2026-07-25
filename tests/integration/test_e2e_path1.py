"""E2E — adapter session lifecycle via the daemon HTTP surface.

Simulates the redrobin.cna adapter's session handling:
  1. Adapter connects (POST /v1/adapters/connect) and gets a session id
  2. Heartbeats keep the session alive (POST /v1/adapters/heartbeat)
  3. Disconnect drops it (POST /v1/adapters/disconnect)
"""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    yield root


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


@pytest.fixture
def auth(_isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


@pytest.fixture
def cs_manifest():
    return {
        "manifest_version": "0.1.0",
        "spec_versions_supported": ">=0.1.0,<0.2.0",
        "name": "cs-aggressor",
        "version": "1.0.0",
        "capabilities": {"preflight_block_console": True},
        "events_emitted": ["beacon.command_input"],
        "events_consumed": ["opsec.verdict"],
        "requirements": {"cobalt_strike": ">=4.12"},
    }


def test_e2e_adapter_connect_returns_session(client, auth, cs_manifest):
    r = client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)
    assert r.status_code == 200
    session_id = r.json()["adapter_session_id"]
    assert session_id.startswith("ses_")


def test_e2e_heartbeat_keeps_session_alive(client, auth, cs_manifest):
    r = client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)
    sid = r.json()["adapter_session_id"]
    # Heartbeat works
    r2 = client.post("/v1/adapters/heartbeat", json={"adapter_session_id": sid}, headers=auth)
    assert r2.status_code == 200


def test_e2e_disconnect_drops_session(client, auth, cs_manifest):
    r = client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)
    sid = r.json()["adapter_session_id"]
    client.post("/v1/adapters/disconnect", json={"adapter_session_id": sid}, headers=auth)
    # Subsequent heartbeat returns 404
    r2 = client.post("/v1/adapters/heartbeat", json={"adapter_session_id": sid}, headers=auth)
    assert r2.status_code == 404
