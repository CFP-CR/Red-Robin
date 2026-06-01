"""redrobin/infrastructure/persistence/context_summary_repo.py — L5 summary store.

Stores :class:`~redrobin.domain.memory.ContextSummary` rows produced by the
periodic ``L5Summarizer`` task. Mirrors the synchronous sqlite3 + lazy-schema
pattern introduced for :class:`EpisodicRepo` and :class:`LongtermRepo` in
Slice 10 so the summariser background loop can write without an event-loop
hop per call.

Schema is created on first connection (idempotent ``CREATE TABLE IF NOT
EXISTS``), so the repo can be instantiated against a fresh path with no
upfront migration step.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from redrobin.domain.memory import ContextSummary


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS context_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id VARCHAR(128) NOT NULL,
    text TEXT NOT NULL,
    window_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_context_summaries_engagement
    ON context_summaries (engagement_id, created_at);
"""


def _iso(ts: datetime) -> str:
    """ISO-8601 string for sqlite3 — sidesteps the deprecated default datetime adapter (Py 3.12)."""
    return ts.isoformat()


def _parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class ContextSummaryRepo:
    """Per-engagement L5 sliding-window summaries (sync, file-backed)."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # No PARSE_DECLTYPES: we serialise timestamps as ISO strings ourselves
        # to dodge the deprecated default datetime adapter on Python 3.12+.
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    def append(self, summary: ContextSummary) -> ContextSummary:
        """Insert one summary row and return it with the persisted timestamp."""
        ts = summary.created_at or datetime.now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO context_summaries (engagement_id, text, window_size, "
                "created_at) VALUES (?, ?, ?, ?)",
                (summary.engagement_id, summary.text, summary.window_size, _iso(ts)),
            )
        return ContextSummary(
            engagement_id=summary.engagement_id,
            text=summary.text,
            window_size=summary.window_size,
            created_at=ts,
        )

    def list_recent(self, engagement_id: str, limit: int = 1) -> list[ContextSummary]:
        """Return the ``limit`` most recent summaries for an engagement (newest first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT engagement_id, text, window_size, created_at "
                "FROM context_summaries WHERE engagement_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (engagement_id, limit),
            ).fetchall()
        return [
            ContextSummary(
                engagement_id=row["engagement_id"],
                text=row["text"],
                window_size=row["window_size"],
                created_at=_parse_iso(row["created_at"]),
            )
            for row in rows
        ]


__all__ = ["ContextSummaryRepo"]
