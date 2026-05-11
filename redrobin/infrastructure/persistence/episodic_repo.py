"""redrobin/infrastructure/persistence/episodic_repo.py — Engagement-specific lessons + raw episodes.

Phase 1 shipped this repo as an async SQLAlchemy facade over the ``lessons``
table (L3 Episodic per spec §4). Slice 10 adds:

* A second constructor that accepts a filesystem ``Path``. When constructed
  from a path the repo opens a sync ``sqlite3`` connection lazily per call
  and provisions both the ``lessons`` and new ``episodes`` tables on first
  use, so the synchronous reducer dispatch can append episodes without an
  event-loop hop per event.
* ``record(...)``/``list_episodes_for_engagement(...)`` — sync writes that
  capture every ``opsec.verdict`` event into the raw episode log. The
  ``LessonPromoter`` aggregates these into structured ``PromotedLesson``
  records stored in ``LongtermRepo``.

The original async API (``add``/``list_for_engagement``/``mark_promoted``) is
preserved for callers built on the SQLAlchemy ``AsyncEngine`` path — those
tests continue to pass unchanged.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from redrobin.domain.memory import Episode, Lesson
from redrobin.infrastructure.persistence.db import lessons


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

    def __init__(self, engine_or_path: AsyncEngine | Path | str):
        # Two modes: AsyncEngine (Phase 1) or filesystem Path (Slice 10 sync).
        if isinstance(engine_or_path, AsyncEngine):
            self._engine: AsyncEngine | None = engine_or_path
            self._path: Path | None = None
        else:
            self._engine = None
            self._path = Path(engine_or_path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_schema()

    # --- Sync sqlite3 helpers (Slice 10) -------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        if self._path is None:
            raise RuntimeError(
                "sync EpisodicRepo methods require a Path-based constructor"
            )
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

    # --- Original async SQLAlchemy API (Phase 1) ------------------------ #

    async def add(
        self,
        *,
        engagement_id: str,
        text: str,
        source: str = "auto-detected",
        created_at: datetime | None = None,
    ) -> Lesson:
        if self._engine is None:
            raise RuntimeError(
                "async EpisodicRepo.add requires an AsyncEngine constructor"
            )
        ts = created_at or datetime.now()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                insert(lessons).values(
                    engagement_id=engagement_id,
                    text=text,
                    source=source,
                    created_at=ts,
                    promoted_to_longterm=False,
                )
            )
        lesson_id = result.inserted_primary_key[0]
        return Lesson(
            id=lesson_id,
            engagement_id=engagement_id,
            text=text,
            source=source,
            created_at=ts,
            promoted_to_longterm=False,
        )

    async def list_for_engagement(self, engagement_id: str) -> list[Lesson]:
        if self._engine is None:
            raise RuntimeError(
                "async EpisodicRepo.list_for_engagement requires an AsyncEngine "
                "constructor"
            )
        async with self._engine.begin() as conn:
            result = await conn.execute(
                select(lessons).where(lessons.c.engagement_id == engagement_id)
            )
            return [
                Lesson(
                    id=row.id,
                    engagement_id=row.engagement_id,
                    text=row.text,
                    source=row.source,
                    created_at=row.created_at,
                    promoted_to_longterm=row.promoted_to_longterm,
                )
                for row in result.fetchall()
            ]

    async def mark_promoted(self, lesson_id: int) -> None:
        if self._engine is None:
            raise RuntimeError(
                "async EpisodicRepo.mark_promoted requires an AsyncEngine constructor"
            )
        async with self._engine.begin() as conn:
            await conn.execute(
                update(lessons).where(lessons.c.id == lesson_id).values(promoted_to_longterm=True)
            )
