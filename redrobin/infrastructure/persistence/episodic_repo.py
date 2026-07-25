"""redrobin/infrastructure/persistence/episodic_repo.py — Engagement-specific episodes (L3).

Single-mode store backed by a filesystem ``Path`` and a sync ``sqlite3``
connection opened lazily per call. Provisions the ``lessons`` and
``episodes`` tables on first use so the synchronous reducer dispatch can
append episodes without an event-loop hop per event.

``record(...)``/``list_episodes_for_engagement(...)`` capture every
``beacon.command_input`` event into the raw episode log, which the L5
summariser then compresses into rolling context for the assistant.

Phase 1's async SQLAlchemy API (``add``/``list_for_engagement``/
``mark_promoted``) was removed in Slice 9 — the production reducer →
summariser flow only ever used the sync API, and no production caller
referenced the async branch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from redrobin.domain.memory import Episode


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id VARCHAR(128) NOT NULL,
    text TEXT NOT NULL,
    source VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    promoted_to_longterm BOOLEAN NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_lessons_engagement_id ON lessons (engagement_id);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id VARCHAR(128) NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    command TEXT NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_episodes_engagement_id ON episodes (engagement_id);
"""


def _iso(ts: datetime) -> str:
    """ISO-8601 string for sqlite3 — avoids the deprecated default datetime adapter (Py 3.12)."""
    return ts.isoformat()


def _parse_iso(value: str | datetime) -> datetime:
    """Parse a stored timestamp back to ``datetime``; tolerate legacy ``datetime`` rows."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class EpisodicRepo:
    """L3 Episodic store — lessons (curated) + episodes (raw verdict log)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # --- Sync sqlite3 helpers ------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        # No PARSE_DECLTYPES: we serialise timestamps as ISO strings ourselves
        # to dodge the deprecated default datetime adapter on Python 3.12+.
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    def record(
        self,
        *,
        engagement_id: str,
        correlation_id: str,
        command: str,
        verdict: str,
        reason: str = "",
        created_at: datetime | None = None,
    ) -> Episode:
        """Append a raw verdict episode (synchronous, file-backed)."""
        ts = created_at or datetime.now()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO episodes (engagement_id, correlation_id, command, "
                "verdict, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (engagement_id, correlation_id, command, verdict, reason, _iso(ts)),
            )
            episode_id = cursor.lastrowid
        return Episode(
            id=episode_id,
            engagement_id=engagement_id,
            correlation_id=correlation_id,
            command=command,
            verdict=verdict,
            reason=reason,
            created_at=ts,
        )

    def list_episodes_for_engagement(self, engagement_id: str) -> list[Episode]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, engagement_id, correlation_id, command, verdict, "
                "reason, created_at FROM episodes WHERE engagement_id = ? "
                "ORDER BY id",
                (engagement_id,),
            ).fetchall()
        return [
            Episode(
                id=row["id"],
                engagement_id=row["engagement_id"],
                correlation_id=row["correlation_id"],
                command=row["command"],
                verdict=row["verdict"],
                reason=row["reason"],
                created_at=_parse_iso(row["created_at"]),
            )
            for row in rows
        ]
