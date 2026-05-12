"""redrobin/infrastructure/persistence/longterm_repo.py — Cross-engagement durable store (L1).

Single-mode store backed by a filesystem ``Path`` and a sync ``sqlite3``
connection. Persists structured ``PromotedLesson`` records keyed by
``(command, verdict)`` so future LLM judgments can retrieve them from L1.

Phase 1's async SQLAlchemy API (``add``/``list_all`` over the ``facts``
table) was removed in Slice 9 — production never used it; the lesson
promoter and Layer B retrieval only call the sync API below.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from redrobin.domain.memory import PromotedLesson


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    source_engagement_id VARCHAR(128),
    created_at DATETIME NOT NULL,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS promoted_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    verdict VARCHAR(16) NOT NULL,
    summary TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_promoted_lessons_command ON promoted_lessons (command);
"""


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class LongtermRepo:
    """L1 Long-term store — facts (curated) + promoted lessons (pattern records)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # --- Sync sqlite3 helpers ------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        # ISO timestamp strings handled in code — bypasses the deprecated
        # default sqlite3 datetime adapter on Python 3.12+.
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    def upsert_lesson(self, lesson: PromotedLesson) -> PromotedLesson:
        """Insert or update a promoted lesson keyed by (command, verdict).

        Idempotent: re-promoting an existing pattern updates the
        ``occurrence_count`` and ``summary`` instead of producing a duplicate
        row.
        """
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM promoted_lessons "
                "WHERE command = ? AND verdict = ?",
                (lesson.command, lesson.verdict),
            ).fetchone()
            if existing is None:
                ts = lesson.created_at or datetime.now()
                cursor = conn.execute(
                    "INSERT INTO promoted_lessons (command, verdict, summary, "
                    "occurrence_count, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        lesson.command,
                        lesson.verdict,
                        lesson.summary,
                        lesson.occurrence_count,
                        _iso(ts),
                    ),
                )
                return PromotedLesson(
                    id=cursor.lastrowid,
                    command=lesson.command,
                    verdict=lesson.verdict,
                    summary=lesson.summary,
                    occurrence_count=lesson.occurrence_count,
                    created_at=ts,
                )
            conn.execute(
                "UPDATE promoted_lessons SET summary = ?, occurrence_count = ? "
                "WHERE id = ?",
                (lesson.summary, lesson.occurrence_count, existing["id"]),
            )
            return PromotedLesson(
                id=existing["id"],
                command=lesson.command,
                verdict=lesson.verdict,
                summary=lesson.summary,
                occurrence_count=lesson.occurrence_count,
                created_at=_parse_iso(existing["created_at"]),
            )

    def find_by_command(
        self, command: str, verdict: str
    ) -> PromotedLesson | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, command, verdict, summary, occurrence_count, "
                "created_at FROM promoted_lessons "
                "WHERE command = ? AND verdict = ?",
                (command, verdict),
            ).fetchone()
        if row is None:
            return None
        return PromotedLesson(
            id=row["id"],
            command=row["command"],
            verdict=row["verdict"],
            summary=row["summary"],
            occurrence_count=row["occurrence_count"],
            created_at=_parse_iso(row["created_at"]),
        )

    def list_lessons(self) -> list[PromotedLesson]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, command, verdict, summary, occurrence_count, "
                "created_at FROM promoted_lessons ORDER BY id"
            ).fetchall()
        return [
            PromotedLesson(
                id=row["id"],
                command=row["command"],
                verdict=row["verdict"],
                summary=row["summary"],
                occurrence_count=row["occurrence_count"],
                created_at=_parse_iso(row["created_at"]),
            )
            for row in rows
        ]
