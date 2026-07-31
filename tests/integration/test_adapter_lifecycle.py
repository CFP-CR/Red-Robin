"""Adapter session lifecycle — connect/heartbeat/disconnect."""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / ".redrobin"))
    (tmp_path / ".redrobin").mkdir(parents=True, exist_ok=True)
    # Reset the session registry singleton between tests for isolation
    from redrobin.application.adapter_sessions import AdapterSessionRegistry
    import redrobin.application.adapter_sessions as adapter_sessions

    monkeypatch.setattr(adapter_sessions, "_REGISTRY", AdapterSessionRegistry())
    yield


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


@pytest.fixture
def auth_header(_isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


@pytest.fixture
def valid_manifest():
    return {
        "manifest_version": "0.1.0",
        "spec_versions_supported": ">=0.1.0,<0.2.0",
        "name": "cs-aggressor",
        "version": "1.0.0",
        "capabilities": {"output_observe": True},
        "events_emitted": ["beacon.command_input"],
        "requirements": {"cobalt_strike": ">=4.12"},
    }


def test_connect_returns_session_id(client, auth_header, valid_manifest):
    r = client.post("/v1/adapters/connect", json={"manifest": valid_manifest}, headers=auth_header)
    assert r.status_code == 200
    sid = r.json()["adapter_session_id"]
    assert sid.startswith("ses_")
    assert len(sid) > len("ses_") + 20  # ULID is 26 chars


def test_connect_rejects_missing_manifest_version(client, auth_header):
    r = client.post(
        "/v1/adapters/connect",
        json={"manifest": {"name": "x", "version": "1.0.0"}},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_connect_rejects_missing_name(client, auth_header):
    r = client.post(
        "/v1/adapters/connect",
        json={"manifest": {"manifest_version": "0.1.0", "version": "1.0.0"}},
        headers=auth_header,
    )
    assert r.status_code == 400


def test_connect_requires_auth(client, valid_manifest):
    r = client.post("/v1/adapters/connect", json={"manifest": valid_manifest})
    assert r.status_code in (401, 422)


def test_heartbeat_updates_session(client, auth_header, valid_manifest):
    r1 = client.post("/v1/adapters/connect", json={"manifest": valid_manifest}, headers=auth_header)
    sid = r1.json()["adapter_session_id"]
    r2 = client.post(
        "/v1/adapters/heartbeat", json={"adapter_session_id": sid}, headers=auth_header
    )
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}


def test_heartbeat_unknown_session_returns_404(client, auth_header):
    r = client.post(
        "/v1/adapters/heartbeat",
        json={"adapter_session_id": "ses_does_not_exist"},
        headers=auth_header,
    )
    assert r.status_code == 404


def test_disconnect_removes_session(client, auth_header, valid_manifest):
    r1 = client.post("/v1/adapters/connect", json={"manifest": valid_manifest}, headers=auth_header)
    sid = r1.json()["adapter_session_id"]
    r2 = client.post(
        "/v1/adapters/disconnect", json={"adapter_session_id": sid}, headers=auth_header
    )
    assert r2.status_code == 200
    # Subsequent heartbeat should 404
    r3 = client.post(
        "/v1/adapters/heartbeat", json={"adapter_session_id": sid}, headers=auth_header
    )
    assert r3.status_code == 404


def test_disconnect_unknown_session_is_idempotent(client, auth_header):
    r = client.post(
        "/v1/adapters/disconnect",
        json={"adapter_session_id": "ses_does_not_exist"},
        headers=auth_header,
    )
    # Disconnect is idempotent — no error even if session doesn't exist
    assert r.status_code == 200
