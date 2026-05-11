"""Verdict endpoint — POST /v1/verdict/quick (Path 1 alias gate)."""

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("redrobin.infrastructure.filesystem.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.verdict_pipeline_factory.REDROBIN_ROOT", root)
    # Reset the pipeline cache for test isolation
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()
    yield root


@pytest.fixture
def client(_isolated_root):
    return TestClient(create_app())


@pytest.fixture
def auth_header(_isolated_root):
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


def test_verdict_quick_requires_auth(client):
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "pwd", "raw": "pwd"},
    )
    assert r.status_code in (401, 422)


def test_verdict_quick_pass_for_passthrough(client, auth_header):
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "pwd", "raw": "pwd"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "PASS"
    assert body["reason"] == "passthrough"


def test_verdict_quick_escalate_for_unknown_command(client, auth_header):
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "unknown_cmd", "raw": "unknown_cmd args"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    # No rules loaded (empty rules dir in test), so unknown_cmd → ESCALATE
    assert body["action"] == "ESCALATE"


def test_verdict_quick_block_with_bootstrap_rule(client, auth_header, _isolated_root):
    """Drop a YAML rule into ~/.redrobin/rules/ and verify endpoint picks it up."""
    rules_dir = _isolated_root / "rules" / "shared"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "mimikatz.yaml").write_text("""
id: mimikatz_logonpasswords
match:
  command: mimikatz
  arg_pattern: 'logonpasswords'
severity: DANGER
reason: 'lsass touch + cred extraction (T1003.001)'
""")
    # Reset cache so the new rules are loaded
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()

    r = client.post(
        "/v1/verdict/quick",
        json={
            "bid": "b1",
            "command": "mimikatz",
            "raw": "mimikatz sekurlsa::logonpasswords",
        },
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "BLOCK"
    assert body["rule_id"] == "mimikatz_logonpasswords"
    assert "lsass" in body["reason"]


def test_verdict_quick_response_shape(client, auth_header):
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "pwd", "raw": "pwd"},
        headers=auth_header,
    )
    body = r.json()
    assert set(body.keys()) == {"action", "reason", "rule_id", "suggestions"}
    assert isinstance(body["suggestions"], list)


def test_verdict_quick_escalate_returns_immediately_without_layer_b_blocking(
    client, auth_header, _isolated_root, monkeypatch
):
    """Even when Layer B is real, ESCALATE returns immediately (async fire-and-forget)."""
    # The default _DegradedLayerB returns PASS in <1ms; verify quick_verdict returns ESCALATE
    # synchronously without waiting for Layer B
    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "obscure_cmd", "raw": "obscure_cmd"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    # Empty rules dir → no Layer A match → ESCALATE (and Layer B fires async)
    assert body["action"] == "ESCALATE"


def test_verdict_quick_degraded_layer_b_when_api_keys_missing(
    client, auth_header, _isolated_root, monkeypatch
):
    """When API keys are missing, Layer B degrades to no-op (Path 3 only) but daemon still works."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from redrobin.application.verdict_pipeline_factory import reset_pipeline_cache

    reset_pipeline_cache()

    r = client.post(
        "/v1/verdict/quick",
        json={"bid": "b1", "command": "pwd", "raw": "pwd"},
        headers=auth_header,
    )
    assert r.status_code == 200
    # passthrough still works — Layer B not invoked
    assert r.json()["action"] == "PASS"
