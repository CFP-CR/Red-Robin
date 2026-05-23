"""Daemon picks up the active engagement from `.active_engagement` file.

Regression guard for the 2026-05-23 T14 smoke finding: `redrobin engagement
use X` was silently ignored by the daemon because Settings only read the
REDROBIN_ENGAGEMENT_SLUG env var. The fix layers EngagementService.current()
as a fallback in start_engagement.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.engagement_service import EngagementService


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    """Point all daemon paths at a tmp_path; clear the engagement env var."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    # Boot validation exits(2) when ANTHROPIC_API_KEY / VOYAGE_API_KEY are
    # missing unless the operator opts into degraded mode. These tests only
    # care about engagement resolution, so opt in.
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    yield tmp_path


def test_daemon_reads_active_engagement_file(isolated_root):
    # 1. Create an engagement and mark it active (mirrors `redrobin engagement
    #    new X && redrobin engagement use X`).
    svc = EngagementService(root=isolated_root)
    svc.new("tc4-smoke")
    svc.use("tc4-smoke")
    assert svc.current() == "tc4-smoke"

    # 2. Start daemon - env var is NOT set; daemon must fall back to file.
    app = create_app()
    with TestClient(app):
        # 3. Lifespan ran; engagement should be resolved.
        # The Engagement dataclass returned by EngagementLoader exposes
        # ``.slug`` (not ``.id``); the loader maps the on-disk YAML key
        # ``id`` written by EngagementService into ``slug`` via its
        # fallback (``data.get("slug", slug)``).
        assert app.state.engagement is not None
        assert app.state.engagement.slug == "tc4-smoke"
        assert app.state.engagement_id == "tc4-smoke"


def test_env_var_overrides_active_file(isolated_root, monkeypatch):
    """REDROBIN_ENGAGEMENT_SLUG takes precedence over the .active_engagement file."""
    svc = EngagementService(root=isolated_root)
    svc.new("file-pick")
    svc.new("env-pick")
    svc.use("file-pick")
    assert svc.current() == "file-pick"

    # Set env var pointing to the other engagement
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "env-pick")

    app = create_app()
    with TestClient(app):
        assert app.state.engagement is not None
        assert app.state.engagement.slug == "env-pick", (
            "env var must override the persistent active-engagement file"
        )


def test_neither_set_falls_back_to_none(isolated_root):
    """With no env var AND no active file, engagement is None (degraded mode)."""
    app = create_app()
    with TestClient(app):
        assert app.state.engagement is None
        # `app.state.engagement_id` may default to "eng-default" via Settings
        # - that's pre-existing behavior and out of scope for this test.
