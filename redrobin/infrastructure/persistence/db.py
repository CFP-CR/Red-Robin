"""redrobin/infrastructure/persistence/db.py — SQLAlchemy Core 2.0 metadata.

Per spec §13.3: SQLAlchemy Core (no ORM ceremony for 6-8 simple tables).
Tables are registered on the shared :data:`metadata` so Alembic's autogen
machinery in ``migrations/env.py`` can diff the live schema against the
declared one.

Note: Slice 12 removed ``make_engine``. The async engine factory lived here
in v0.0 when ``LessonsRepo`` used SQLAlchemy Core directly; today every
repo (longterm/episodic/context_summary) drives ``aiosqlite`` directly with
hand-rolled DDL so the engine factory had no callers.
"""

from __future__ import annotations

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


# Slice 10 — raw episodes (one per issued command), the L3 audit trail.
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
