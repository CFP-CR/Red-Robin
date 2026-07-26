"""Persistence unit tests — sync sqlite3 API for EpisodicRepo.

The original async SQLAlchemy API was removed in Slice 9 of the v0.1.x
hardening pass (single-mode repos). Tests below exercise the surviving
sync surface: ``EpisodicRepo.record`` / ``list_episodes_for_engagement``
plus the gate-era schema migration. ``LongtermRepo``'s surviving surface
(the operator-correction lessons) is covered in ``test_operator_lessons.py``;
its ``PromotedLesson`` API went with the pre-flight gate.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo


def test_episodic_record_and_list(tmp_path) -> None:
    repo = EpisodicRepo(tmp_path / "episodic.db")
    episode = repo.record(
        engagement_id="acme-q2",
        correlation_id="corr-1",
        command="whoami",
    )
    assert episode.id is not None
    assert episode.engagement_id == "acme-q2"
    assert episode.correlation_id == "corr-1"

    episodes = repo.list_episodes_for_engagement("acme-q2")
    assert len(episodes) == 1
    assert episodes[0].command == "whoami"


def test_episodic_isolates_per_engagement(tmp_path) -> None:
    repo = EpisodicRepo(tmp_path / "episodic.db")
    repo.record(
        engagement_id="acme-q2",
        correlation_id="c1",
        command="nanodump",
    )
    repo.record(
        engagement_id="globex-q3",
        correlation_id="c2",
        command="rubeus",
    )

    acme = repo.list_episodes_for_engagement("acme-q2")
    globex = repo.list_episodes_for_engagement("globex-q3")
    assert {ep.command for ep in acme} == {"nanodump"}
    assert {ep.command for ep in globex} == {"rubeus"}


# --- legacy-schema migration ------------------------------------------------ #

# The episodes table exactly as the pre-flight gate wrote it. `verdict` is NOT
# NULL with NO default, so an INSERT that omits it fails outright.
_LEGACY_EPISODES_SQL = """
CREATE TABLE episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    command TEXT NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _legacy_db(path) -> None:
    """Write an episodic.db carrying the OLD (gate-era) episodes schema + one row."""
    con = sqlite3.connect(path)
    try:
        con.executescript(_LEGACY_EPISODES_SQL)
        con.execute(
            "INSERT INTO episodes (engagement_id, correlation_id, command, verdict,"
            " reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("vantage", "c-old", "whoami", "PASS", "standard recon", "2026-07-20T10:00:00"),
        )
        con.commit()
    finally:
        con.close()


def test_episodic_migrates_a_legacy_verdict_schema(tmp_path) -> None:
    """A DB created BEFORE the gate was removed must keep working.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing table, so without a
    migration the gate-era ``verdict VARCHAR(16) NOT NULL`` column survives and the
    narrowed INSERT raises ``NOT NULL constraint failed: episodes.verdict`` on EVERY
    command. The reducer swallows that exception, so on a live daemon the symptom is
    silent total loss of L3 — which is why this case is tested against a legacy file
    rather than a fresh ``tmp_path`` database.
    """
    db = tmp_path / "episodic.db"
    _legacy_db(db)

    repo = EpisodicRepo(db)  # constructing the repo runs the migration

    # the gate columns are gone
    con = sqlite3.connect(db)
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(episodes)")}
    finally:
        con.close()
    assert "verdict" not in columns and "reason" not in columns

    # writing works against the migrated table
    episode = repo.record(engagement_id="vantage", correlation_id="c-new", command="ls")
    assert episode.id is not None

    # and the pre-existing row was NOT destroyed
    episodes = repo.list_episodes_for_engagement("vantage")
    assert [e.command for e in episodes] == ["whoami", "ls"]
    assert episodes[0].correlation_id == "c-old"
    assert episodes[0].created_at == datetime(2026, 7, 20, 10, 0, 0)


def test_episodic_migration_is_idempotent(tmp_path) -> None:
    """Re-opening an already-migrated DB is a no-op (every boot after the first)."""
    db = tmp_path / "episodic.db"
    _legacy_db(db)

    EpisodicRepo(db)
    repo = EpisodicRepo(db)  # second construction must not raise
    repo.record(engagement_id="vantage", correlation_id="c-2", command="ps")

    assert [e.command for e in repo.list_episodes_for_engagement("vantage")] == ["whoami", "ps"]
