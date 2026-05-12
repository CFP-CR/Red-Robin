"""E2E Path 1 — alias gate flow via daemon HTTP surface.

Simulates the redrobin.cna alias body's behavior:
  1. Adapter connects (POST /v1/adapters/connect)
  2. Alias body queries (POST /v1/verdict/quick)
  3. Daemon evaluates Stage 0-2 of verdict pipeline (passthrough -> scope -> Layer A)
  4. Returns PASS / WARN / BLOCK / ESCALATE
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()
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


def _bootstrap_rules(root: Path) -> None:
    """Copy a minimal bootstrap rule to ~/.redrobin/rules/shared/ for tests."""
    rules_dir = root / "rules" / "shared"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "mimikatz.yaml").write_text("""
id: mimikatz_logonpasswords
match:
  command: mimikatz
  arg_pattern: '(?i)logonpasswords|sekurlsa'
severity: DANGER
reason: 'lsass touch + cred extraction (T1003.001)'
""")
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()


def test_e2e_adapter_connect_then_verdict_passthrough(client, auth, cs_manifest):
    # Step 1: adapter handshake
    r = client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)
    assert r.status_code == 200
    session_id = r.json()["adapter_session_id"]
    assert session_id.startswith("ses_")

    # Step 2: alias body queries verdict for passthrough command
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "pwd", "raw": "pwd"},
        headers=auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "PASS"
    assert body["reason"] == "passthrough"


def test_e2e_alias_gate_blocks_mimikatz_with_bootstrap_rule(
    client, auth, cs_manifest, _isolated_root
):
    # Set up: bootstrap rules + connect adapter
    _bootstrap_rules(_isolated_root)
    client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)

    # Alias body fires for `mimikatz logonpasswords`
    r = client.post(
        "/v1/verdict/quick",
        json={
            "bid": "b1",
            "command": "mimikatz",
            "raw": "mimikatz sekurlsa::logonpasswords",
        },
        headers=auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "BLOCK"
    assert body["rule_id"] == "mimikatz_logonpasswords"
    assert "lsass" in body["reason"]


def test_e2e_unknown_command_escalates(client, auth, cs_manifest):
    client.post("/v1/adapters/connect", json={"manifest": cs_manifest}, headers=auth)
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "obscure_cmd", "raw": "obscure_cmd args"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["action"] == "ESCALATE"


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
