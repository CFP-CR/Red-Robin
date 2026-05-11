"""redrobin/infrastructure/persistence/longterm_repo.py — Cross-engagement durable facts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from redrobin.domain.memory import Fact
from redrobin.infrastructure.persistence.db import facts


class LongtermRepo:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def add(
        self,
        *,
        text: str,
        source_engagement_id: str | None = None,
        created_at: datetime | None = None,
        embedding: bytes | None = None,
    ) -> Fact:
        ts = created_at or datetime.now()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                insert(facts).values(
                    text=text,
                    source_engagement_id=source_engagement_id,
                    created_at=ts,
                    embedding=embedding,
                )
            )
        fact_id = result.inserted_primary_key[0]
        return Fact(
            id=fact_id,
            text=text,
            source_engagement_id=source_engagement_id,
            created_at=ts,
            embedding=None,  # not deserialized back to floats here; that's the vector store's job
        )

    async def list_all(self) -> list[Fact]:
        async with self._engine.begin() as conn:
            result = await conn.execute(select(facts))
            return [
                Fact(
                    id=row.id,
                    text=row.text,
                    source_engagement_id=row.source_engagement_id,
                    created_at=row.created_at,
                    embedding=None,
                )
                for row in result.fetchall()
            ]
