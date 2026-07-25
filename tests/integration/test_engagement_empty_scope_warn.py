"""#142: a loaded engagement with empty in_scope must surface LOUDLY.

The daemon sets ``app.state.scope_empty`` (read by the startup banner) and logs
a WARNING. Removing the pre-flight gate changed the *consequence* of an empty
scope — nothing is BLOCKED any more, the assistant simply reasons without a
target boundary — but the flag and the warning both survive, because the
operator still needs to know their scope is undefined. Without this file the
``scope_empty = True`` branch and the WARNING have no coverage at all, which is
exactly the regression #142 was filed for.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from redrobin.api.app import create_app
from redrobin.application.engagement_service import EngagementService

# Distinctive fragments of the live WARNING (redrobin/api/lifespan_helpers.py).
# Matching on these — rather than on any record containing "empty" — is what
# makes the assertion fail if the warning stops firing or is gutted.
_WARN_FRAGMENTS = ("EMPTY in_scope", "no target boundary")


def _empty_scope_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and all(frag in r.getMessage() for frag in _WARN_FRAGMENTS)
    ]


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    yield tmp_path


def test_empty_scope_engagement_sets_flag_and_warns(isolated_root, caplog):
    """The True branch + the WARNING. This is the #142 regression guard."""
    svc = EngagementService(root=isolated_root)
    svc.new("empty-eng")  # no --in-scope -> empty scope
    svc.use("empty-eng")

    app = create_app()
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            assert app.state.engagement is not None
            assert app.state.scope_empty is True

    matched = _empty_scope_warnings(caplog)
    assert matched, (
        "empty-scope WARNING not emitted. Saw: "
        f"{[r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]}"
    )
    # The record must identify WHICH engagement is unscoped — the slug is a
    # %r argument, so this also pins that the args actually interpolate.
    assert any("empty-eng" in msg for msg in matched), matched


def test_scoped_engagement_flag_false_and_does_not_warn(isolated_root, caplog):
    """Paired negative: a scoped engagement must NOT trip the flag or the warning."""
    svc = EngagementService(root=isolated_root)
    svc.new("scoped-eng", in_scope=["corp.local"])
    svc.use("scoped-eng")

    app = create_app()
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            assert app.state.scope_empty is False

    assert not _empty_scope_warnings(caplog), (
        "empty-scope WARNING fired for a properly scoped engagement"
    )


def test_no_engagement_flag_false(isolated_root):
    # Wildcard fallback (no engagement) is a DIFFERENT case (already banner-warned);
    # scope_empty is reserved for "engagement loaded but scope empty".
    app = create_app()
    with TestClient(app):
        assert app.state.engagement is None
        assert app.state.scope_empty is False
