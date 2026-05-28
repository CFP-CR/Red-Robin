"""Integration tests for the events_router dedup gate (B3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Boot the daemon in degraded mode against an isolated root."""
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-b3")
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    from redrobin.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _token() -> str:
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    return get_or_create_token()


def test_lifespan_attaches_dedup_keys(daemon) -> None:
    app, _ = daemon
    from redrobin.application.dedup_keys import DedupKeys

    assert hasattr(app.state, "dedup_keys")
    assert isinstance(app.state.dedup_keys, DedupKeys)
