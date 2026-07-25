"""E2E: TUI ← daemon event journey over the WebSocket.

Slice 15 Task 15.4 originally exercised the operator's primary feedback loop as
``POST /v1/verdict/quick → VerdictPipeline → bus.publish(opsec.verdict) → WS
/v1/tui frame``. That verdict round-trip died with the pre-flight gate, so the
only test here was removed with it.

The daemon fixture below is deliberately kept: this journey should be rebuilt
around a surviving event path (the capture path publishing ``beacon.*`` frames
to ``/v1/tui``) rather than re-invented from scratch.
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
