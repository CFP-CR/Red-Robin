"""E2E: TUI dispatch → daemon verdict → TUI sees verdict event over WS.

Slice 15 Task 15.4. Exercises the full real path:

    POST /v1/verdict/quick (HTTP) → VerdictPipeline.quick_verdict
                                  → bus.publish(opsec.verdict layer_a)
                                  → WS /v1/tui frame on subscriber queue.

This is the "smoke test we wish we'd written in Phase 1" for the operator's
primary feedback loop: every command they dispatch through the TUI must
surface a verdict line on the same socket within the request lifecycle.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app


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

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def test_tui_full_loop(daemon):
    app, client = daemon
    token = _token()

    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"

        # Operator dispatches a verdict via the alias-body HTTP path — the same
        # endpoint the TUI's OperatorDispatcher hits in production.
        resp = client.post(
            "/v1/verdict/quick",
            json={
                "source_event_id": "c-tui-1",
                "bid": "B-1",
                "command": "whoami",
                "raw": "whoami",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # The verdict pipeline publishes opsec.verdict layer_a *before*
        # returning the HTTP response (see VerdictPipeline.quick_verdict). The
        # WS subscriber must have the frame queued by the time we receive.
        evt = ws.receive_json()
        assert evt["type"] == "opsec.verdict"
        assert evt["payload"]["correlation_id"] == "c-tui-1"
        assert evt["payload"]["beacon_id"] == "B-1"
        assert evt["payload"]["command"] == "whoami"
        assert evt["payload"]["source"] == "layer_a"
        # Verdict value depends on rules + passthrough; ``whoami`` is not in
        # the default passthrough whitelist and no rule is loaded, so it
        # ESCALATEs. Assert the action is one of the published-shape values.
        assert evt["payload"]["verdict"] in {"PASS", "WARN", "BLOCK", "ESCALATE"}
