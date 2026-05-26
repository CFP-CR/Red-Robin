"""Engagement isolation — each engagement gets its own working_memory + episodic DB.

Today's daemon ignores the active engagement and writes everything to root paths
tagged with `eng-default` (the settings default). This test fixes the contract:
after `redrobin engagement use X`, the daemon must write to
`~/.redrobin/engagements/X/{working_memory.json, episodic.db}` and tag every
episode with `engagement_id='X'`.

Until Task B2 lands, all three tests FAIL. That's the intended RED state.

References:
  - lifespan_helpers.py:131 — current reducer captures `settings.engagement_id`
  - working_memory_reducer.py:75,178 — engagement_id frozen at __init__
  - app.py lifespan — start_working_memory runs BEFORE start_engagement
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


def _publish_verdict(
    client, app, *, beacon_id: str, correlation_id: str, command: str, verdict: str
):
    """Publish a Layer A opsec.verdict event onto the bus via the TestClient portal."""
    client.portal.call(
        app.state.event_bus.publish,
        {
            "type": "opsec.verdict",
            "payload": {
                "correlation_id": correlation_id,
                "beacon_id": beacon_id,
                "command": command,
                "verdict": verdict,
                "reason": f"test verdict for {command}",
                "rule_id": None,
                "source": "layer_a",
            },
        },
    )


def _count_episodes(db_path: Path, *, engagement_id: str | None = None) -> int:
    """Return episode-count from a sqlite db file, optionally filtered by engagement_id.

    Returns 0 if the file doesn't exist or has no `episodes` table.
    """
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(str(db_path))
        try:
            if engagement_id is None:
                row = con.execute("SELECT COUNT(*) FROM episodes").fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(*) FROM episodes WHERE engagement_id = ?",
                    (engagement_id,),
                ).fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()
    except sqlite3.OperationalError:
        return 0  # `episodes` table doesn't exist yet


def test_episode_written_to_active_engagement_db(isolated_root):
    """After `engagement use X`, verdicts land in engagements/X/episodic.db tagged X."""
    svc = EngagementService(root=isolated_root)
    svc.new("eng-a")
    svc.use("eng-a")

    app = create_app()
    with TestClient(app) as client:
        # Sanity: the engagement-resolution fix from a02d283 should have set this
        assert app.state.engagement is not None, (
            "start_engagement did not resolve eng-a — check a02d283 fix"
        )
        assert app.state.engagement_id == "eng-a"

        _publish_verdict(
            client,
            app,
            beacon_id="bid_1",
            correlation_id="c_1",
            command="mimikatz",
            verdict="BLOCK",
        )
        # Allow the reducer's async loop one tick to process the event
        import time

        time.sleep(0.5)

    # After lifespan close, the file must exist at the per-engagement path
    per_eng_db = isolated_root / "engagements" / "eng-a" / "episodic.db"
    root_db = isolated_root / "episodic.db"

    assert _count_episodes(per_eng_db, engagement_id="eng-a") >= 1, (
        f"expected eng-a episode in {per_eng_db}, "
        f"found {_count_episodes(per_eng_db, engagement_id='eng-a')}; "
        f"root episodic.db has {_count_episodes(root_db)} total"
    )
    assert _count_episodes(root_db) == 0, (
        "root episodic.db should be empty when engagement is active; "
        f"found {_count_episodes(root_db)} rows"
    )


def test_two_engagements_do_not_cross_contaminate(isolated_root):
    """Episodes from eng-a do not show up under eng-b's DB."""
    svc = EngagementService(root=isolated_root)
    svc.new("eng-a")
    svc.new("eng-b")

    # First run under eng-a
    svc.use("eng-a")
    app = create_app()
    with TestClient(app) as client:
        _publish_verdict(
            client,
            app,
            beacon_id="bid_a",
            correlation_id="c_a",
            command="mimikatz",
            verdict="BLOCK",
        )
        import time

        time.sleep(0.5)

    # Then switch to eng-b and run again
    svc.use("eng-b")
    app = create_app()
    with TestClient(app) as client:
        _publish_verdict(
            client,
            app,
            beacon_id="bid_b",
            correlation_id="c_b",
            command="hashdump",
            verdict="BLOCK",
        )
        import time

        time.sleep(0.5)

    db_a = isolated_root / "engagements" / "eng-a" / "episodic.db"
    db_b = isolated_root / "engagements" / "eng-b" / "episodic.db"

    # Each engagement sees only its own row
    assert _count_episodes(db_a, engagement_id="eng-a") == 1, (
        f"eng-a should have 1 row, found {_count_episodes(db_a, engagement_id='eng-a')}"
    )
    assert _count_episodes(db_a, engagement_id="eng-b") == 0, "eng-a db must NOT contain eng-b rows"
    assert _count_episodes(db_b, engagement_id="eng-b") == 1, (
        f"eng-b should have 1 row, found {_count_episodes(db_b, engagement_id='eng-b')}"
    )
    assert _count_episodes(db_b, engagement_id="eng-a") == 0, "eng-b db must NOT contain eng-a rows"


def test_degraded_mode_no_engagement_falls_back_to_root(isolated_root):
    """Without an active engagement, episodes still land in root episodic.db.

    Backwards compatibility for tests/CI that don't set up an engagement.
    """
    # No engagement created or used. Daemon starts in degraded scope mode.
    app = create_app()
    with TestClient(app) as client:
        assert app.state.engagement is None, "no engagement should be active"

        _publish_verdict(
            client, app, beacon_id="bid_x", correlation_id="c_x", command="ls", verdict="PASS"
        )
        import time

        time.sleep(0.5)

    root_db = isolated_root / "episodic.db"
    assert _count_episodes(root_db) >= 1, (
        f"degraded mode should still write to root db, found {_count_episodes(root_db)}"
    )
