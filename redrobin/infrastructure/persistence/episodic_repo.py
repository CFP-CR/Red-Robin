"""redrobin/infrastructure/persistence/episodic_repo.py — Engagement-specific lessons."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from redrobin.domain.memory import Lesson
from redrobin.infrastructure.persistence.db import lessons


class EpisodicRepo:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def add(
        self,
        *,
        engagement_id: str,
        text: str,
        source: str = "auto-detected",
        created_at: datetime | None = None,
    ) -> Lesson:
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
        async with self._engine.begin() as conn:
            await conn.execute(
                update(lessons).where(lessons.c.id == lesson_id).values(promoted_to_longterm=True)
            )
