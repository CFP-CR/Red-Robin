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

import pytest

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


def test_episodic_migration_demands_sqlite_335_by_name(tmp_path, monkeypatch) -> None:
    """On SQLite < 3.35 the migration must fail with a message that explains itself.

    ``ALTER TABLE ... DROP COLUMN`` landed in 3.35. A peer operator on a
    distro-packaged Python (RHEL 8 ships 3.26, Ubuntu 20.04 ships 3.31) otherwise
    gets a bare ``OperationalError: near "DROP": syntax error`` at boot, naming
    neither the requirement nor the fix. Must still be a hard failure — swallowing
    it would leave every episode write raising NOT NULL, i.e. silent total L3 loss.
    """
    db = tmp_path / "episodic.db"
    _legacy_db(db)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 31, 1))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.31.1")

    with pytest.raises(RuntimeError) as exc:
        EpisodicRepo(db)

    message = str(exc.value)
    assert "3.35" in message  # the requirement
    assert "3.31.1" in message  # what is actually running
    assert "episodes" in message  # which table is stuck


def test_episodic_old_sqlite_is_fine_when_no_migration_is_due(tmp_path, monkeypatch) -> None:
    """The guard is scoped to the migration, not to the repo.

    An old SQLite build with a fresh (or already-migrated) database issues no
    ``DROP COLUMN`` at all, so it must keep working rather than be locked out.
    """
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 31, 1))

    repo = EpisodicRepo(tmp_path / "episodic.db")  # fresh DB — no legacy columns
    episode = repo.record(engagement_id="vantage", correlation_id="c1", command="whoami")

    assert episode.id is not None
