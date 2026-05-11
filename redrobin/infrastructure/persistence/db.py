"""redrobin/infrastructure/persistence/db.py — SQLAlchemy Core 2.0 metadata + async engine factory.

Per spec §13.3: SQLAlchemy Core (no ORM ceremony for 6-8 simple tables).
Async engines per database (longterm.db shared, episodic.db per-engagement).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()


# L3 Episodic — per-engagement lessons table
lessons = Table(
    "lessons",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("engagement_id", String(128), nullable=False, index=True),
    Column("text", Text, nullable=False),
    Column("source", String(32), nullable=False),  # "operator-tagged" | "auto-detected"
    Column("created_at", DateTime, nullable=False),
    Column("promoted_to_longterm", Boolean, nullable=False, default=False),
)


# L1 Long-term — cross-engagement durable facts
facts = Table(
    "facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("text", Text, nullable=False),
    Column("source_engagement_id", String(128), nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("embedding", LargeBinary, nullable=True),  # serialized numpy float32 array
)


# Slice 10 — raw OPSEC verdict episodes feeding the LessonPromoter.
# Lives in the same database file as ``lessons`` (per-engagement episodic store).
episodes = Table(
    "episodes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("engagement_id", String(128), nullable=False, index=True),
    Column("correlation_id", String(128), nullable=False),
    Column("command", Text, nullable=False),
    Column("verdict", String(16), nullable=False),  # ALLOW|WARN|BLOCK|UNKNOWN
    Column("reason", Text, nullable=False, default=""),
    Column("created_at", DateTime, nullable=False),
)


# Slice 10 — structured (command, verdict) patterns promoted from recurring
# episodes. Lives in the long-term database alongside ``facts``.
promoted_lessons = Table(
    "promoted_lessons",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("command", Text, nullable=False, index=True),
    Column("verdict", String(16), nullable=False),
    Column("summary", Text, nullable=False),
    Column("occurrence_count", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
)


def make_engine(db_path: Path | str) -> AsyncEngine:
    """Construct an async aiosqlite engine for the given path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path.absolute()}"
    return create_async_engine(url, future=True)
