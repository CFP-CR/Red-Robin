"""#142: a loaded engagement with empty in_scope must surface LOUDLY, not
silently deny-all. The daemon sets app.state.scope_empty and logs a WARNING."""

from __future__ import annotations

import logging

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
    yield tmp_path


def test_empty_scope_engagement_sets_flag_and_warns(isolated_root, caplog):
    svc = EngagementService(root=isolated_root)
    svc.new("empty-eng")  # no --in-scope -> empty scope
    svc.use("empty-eng")

    app = create_app()
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            assert app.state.engagement is not None
            assert app.state.scope_empty is True
    assert any("empty" in r.message.lower() for r in caplog.records)


def test_scoped_engagement_flag_false(isolated_root):
    svc = EngagementService(root=isolated_root)
    svc.new("scoped-eng", in_scope=["corp.local"])
    svc.use("scoped-eng")

    app = create_app()
    with TestClient(app):
        assert app.state.scope_empty is False


def test_no_engagement_flag_false(isolated_root):
    # Wildcard fallback (no engagement) is a DIFFERENT case (already banner-warned);
    # scope_empty is reserved for "engagement loaded but scope empty".
    app = create_app()
    with TestClient(app):
        assert app.state.engagement is None
        assert app.state.scope_empty is False
