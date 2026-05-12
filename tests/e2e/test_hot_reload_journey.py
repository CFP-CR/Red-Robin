"""E2E: operator drops a new YAML rule → daemon picks it up → blocks the matching command.

Slice 15 Task 15.1. Exercises the full real path:

    filesystem (rules dir) → watchdog → RulesLoader.load_all() → LayerA.update_rules()
                                                                       ↓
    POST /v1/verdict/quick → VerdictPipeline → LayerA evaluate → BLOCK

The test uses the native Red Robin YAML rule format (``match.command`` +
``severity`` + ``reason``) rather than Sigma because the Phase 1 Sigma stub
loader sets ``match.command`` to the file stem — a Sigma rule named
``exotic.yaml`` would only match command ``exotic``, never ``exotic_tool``.
Full Sigma matching lands in a later Phase 2 slice.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    rules_dir = tmp_path / "rules" / "shared"
    rules_dir.mkdir(parents=True)

    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-e2e")

    app = create_app()
    with TestClient(app) as client:
        yield app, client, rules_dir


def _auth_headers() -> dict[str, str]:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return {"Authorization": f"Bearer {get_or_create_token()}"}


def test_hot_reload_blocks_after_new_rule(daemon):
    app, client, rules_dir = daemon
    auth = _auth_headers()

    # Pre-rule: command has no match in Layer A and isn't in the passthrough
    # whitelist → ESCALATE (fail-open per spec §1.2.1, Layer B picks up async).
    resp1 = client.post(
        "/v1/verdict/quick",
        json={
            "source_event_id": "c-1",
            "bid": "B-1",
            "command": "exotic_tool",
            "raw": "exotic_tool",
        },
        headers=auth,
    )
    assert resp1.status_code == 200
    assert resp1.json()["action"] in {"PASS", "ESCALATE"}

    # Drop a native Red Robin YAML rule blocking the command.
    rule = rules_dir / "exotic.yaml"
    rule.write_text(
        "id: rule-exotic-001\n"
        "match:\n"
        "  command: exotic_tool\n"
        "severity: DANGER\n"
        "reason: 'exotic_tool flagged by E2E hot-reload test'\n"
    )

    # Wait for the watchdog tick + RulesLoader.load_all() + LayerA.update_rules().
    deadline = time.time() + 5.0
    while time.time() < deadline:
        rules = app.state.rules_loader.list_rules()
        if any(r.id == "rule-exotic-001" for r in rules):
            break
        time.sleep(0.05)
    else:
        pytest.fail(
            "hot-reload did not pick up rule within 5s; loader rules=%r"
            % [r.id for r in app.state.rules_loader.list_rules()]
        )

    # Re-submit the same command — Layer A now matches the new rule, BLOCK.
    resp2 = client.post(
        "/v1/verdict/quick",
        json={
            "source_event_id": "c-2",
            "bid": "B-1",
            "command": "exotic_tool",
            "raw": "exotic_tool",
        },
        headers=auth,
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["action"] == "BLOCK", body
    assert body["rule_id"] == "rule-exotic-001"
    assert "exotic_tool" in body["reason"]
