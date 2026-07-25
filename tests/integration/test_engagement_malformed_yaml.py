"""Daemon must survive malformed `engagement.yaml` by falling back to wildcard scope.

Pre-existing: lifespan_helpers.py:start_engagement catches only FileNotFoundError.
A malformed YAML, PermissionError, or UnicodeDecodeError crashes uvicorn at
startup with a Python traceback. This test pins the contract that those error
classes also fall through to the wildcard-scope degraded mode (consistent with
the FileNotFoundError handler).

References:
  - lifespan_helpers.py:start_engagement (current FileNotFoundError-only catch)
  - backlog.md #137 (defensive hardening — broaden exception handler)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_ID", raising=False)
    yield tmp_path


def test_malformed_yaml_falls_back_to_wildcard_scope(isolated_root):
    """Active engagement with malformed engagement.yaml → daemon boots, scope is wildcard."""
    svc = EngagementService(root=isolated_root)
    svc.new("bad-yaml-engagement")
    svc.use("bad-yaml-engagement")

    # Corrupt the engagement.yaml — leave the file present but with invalid YAML
    eng_yaml = isolated_root / "engagements" / "bad-yaml-engagement" / "engagement.yaml"
    eng_yaml.write_text("this: is: not: valid: yaml: at: all: [unclosed\n")

    # Daemon must boot — no crash from yaml.YAMLError propagation
    app = create_app()
    with TestClient(app) as _client:
        # Engagement load failed silently; app.state.engagement is None,
        # scope falls back to wildcard. app.state.engagement_id may still
        # carry the slug from start_working_memory (which reads it from
        # .active_engagement); that's acceptable — the contract is "don't
        # crash", not "scrub state".
        assert app.state.engagement is None, (
            "malformed YAML must yield engagement=None, not propagate a crash"
        )
        # Daemon is still fully booted and serving.
        assert app.state.ready is True


def test_unreadable_yaml_falls_back_to_wildcard_scope(isolated_root):
    """engagement.yaml present but unreadable (encoding error) → wildcard scope, no crash."""
    svc = EngagementService(root=isolated_root)
    svc.new("bad-encoding-engagement")
    svc.use("bad-encoding-engagement")

    # Write bytes that aren't valid UTF-8
    eng_yaml = isolated_root / "engagements" / "bad-encoding-engagement" / "engagement.yaml"
    eng_yaml.write_bytes(b"\xff\xfe\x00\x00 not utf-8 \x80\x81\x82")

    app = create_app()
    with TestClient(app) as _client:
        assert app.state.engagement is None
        assert app.state.ready is True
