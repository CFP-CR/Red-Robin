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

# One test here drives the CLI's dotenv load, which writes os.environ permanently
# (issue #51). Cheap to apply module-wide, and it keeps the .env `init` writes
# from configuring the rest of the session.
pytestmark = pytest.mark.usefixtures("restore_environ")


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    """Point all daemon paths at a tmp_path; clear the engagement env var."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
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
        # 3. Lifespan ran; engagement should be resolved. As of Plan H (#135)
        #    the writer emits `slug` directly (one unified schema), so this no
        #    longer relies on the loader's id->slug fallback.
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


def test_engagement_use_after_init_is_not_a_no_op(isolated_root, monkeypatch):
    """The operator journey: `init` -> `engagement use <other>` -> daemon boot.

    This is the whole-flow version of the guard above. `init` used to write
    ``REDROBIN_ENGAGEMENT_SLUG`` into the .env it generates; the CLI loads that
    .env at command entry, so the env var was set for every later command and
    it OUTRANKS the ``.active_engagement`` marker — `redrobin engagement use`
    wrote the marker and was then silently ignored, forever, for anyone who ran
    the documented setup.
    """
    from redrobin.cli import _load_env_for_cli
    from redrobin.cli_helpers.init_cmd import run_init

    assert (
        run_init(
            root=isolated_root,
            anthropic_key="k1",
            voyage_key="k2",
            engagement_slug="op-init",
            interactive=False,
        )
        == 0
    )

    svc = EngagementService(root=isolated_root)
    svc.new("op-second")
    svc.use("op-second")

    # What every `redrobin <cmd>` does at entry: load the .env init just wrote.
    # Keep the assistant out of it — this test is about engagement resolution,
    # and the generated .env now switches the assistant on.
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    _load_env_for_cli()

    app = create_app()
    with TestClient(app):
        assert app.state.engagement is not None
        assert app.state.engagement.slug == "op-second", (
            "`engagement use` must win over whatever engagement `init` created"
        )


def test_neither_set_falls_back_to_none(isolated_root):
    """With no env var AND no active file, engagement is None (degraded mode)."""
    app = create_app()
    with TestClient(app):
        assert app.state.engagement is None
        # `app.state.engagement_id` may default to "eng-default" via Settings
        # - that's pre-existing behavior and out of scope for this test.
