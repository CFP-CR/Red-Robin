"""E2E: Layer A verdict → Layer B retroactive override → TUI sees both events.

Slice 15 Task 15.3. Exercises the full real path:

    bus.publish(layer_a verdict)           → TUI WS gets frame #1 (source=layer_a)
    pipeline._on_retroactive(...)          → bus.publish(layer_b correction)
                                           → TUI WS gets frame #2 (source=layer_b,
                                                                   previous_verdict set)

The retroactive hook is the same method the factory binds onto
``LayerBEngine.on_retroactive_verdict``; calling it directly proves the bus
contract holds end-to-end. Slice 5 confirmed kwargs: ``correlation_id,
original_verdict, new_verdict, reason``.
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
    # Slice 3: opt into degraded Layer B explicitly. No API keys here.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def test_retroactive_verdict_emits_second_event(daemon):
    app, client = daemon
    token = _token()

    with client.websocket_connect(f"/v1/tui?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "working_memory_snapshot"

        # Simulate Layer A path: publish opsec.verdict layer_a directly onto
        # the daemon's bus. portal.call schedules the coroutine on the daemon
        # loop and blocks until it completes — the bus publish puts the event
        # on every subscriber's queue before returning.
        client.portal.call(
            app.state.event_bus.publish,
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "c-retro",
                    "verdict": "PASS",
                    "source": "layer_a",
                    "command": "ipconfig",
                },
            },
        )
        e1 = ws.receive_json()
        assert e1["type"] == "opsec.verdict"
        assert e1["payload"]["correlation_id"] == "c-retro"
        assert e1["payload"]["verdict"] == "PASS"
        assert e1["payload"]["source"] == "layer_a"

        # Simulate Layer B retroactive via the pipeline hook — same path the
        # real LayerBEngine.on_retroactive_verdict callback fires.
        client.portal.call(
            lambda: app.state.verdict_pipeline._on_retroactive(
                correlation_id="c-retro",
                original_verdict="PASS",
                new_verdict="BLOCK",
                reason="post-hoc EDR risk",
            )
        )
        e2 = ws.receive_json()
        assert e2["type"] == "opsec.verdict"
        assert e2["payload"]["correlation_id"] == "c-retro"
        assert e2["payload"]["verdict"] == "BLOCK"
        assert e2["payload"]["previous_verdict"] == "PASS"
        assert e2["payload"]["source"] == "layer_b"
        assert e2["payload"]["reason"] == "post-hoc EDR risk"
