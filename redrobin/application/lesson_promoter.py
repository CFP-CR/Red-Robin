"""redrobin/application/lesson_promoter.py — promote recurring verdict patterns to L1.

The promoter reads raw episodes from ``EpisodicRepo`` for a single engagement,
groups them by ``(command, verdict)``, and upserts a structured
``PromotedLesson`` into ``LongtermRepo`` for every pattern that:

1. Recurs at or above ``min_occurrences`` times, and
2. Has verdict in ``{BLOCK, WARN}`` (ALLOW verdicts are not actionable
   lessons — capturing them would dilute L1 retrieval).

The upsert is idempotent: a pattern that has already been promoted gets its
``occurrence_count`` and ``summary`` updated in place rather than producing
duplicate rows. ``promote(...)`` returns the number of **new** lesson rows
inserted on this call.

Closes phase-2 gap #15 — Phase 1 wired the repos but no caller exercised
them, so L1 was permanently empty.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from redrobin.domain.memory import Episode, PromotedLesson
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo

logger = logging.getLogger(__name__)

# Promotion is gated on actionable verdicts only — ALLOW patterns are noise.
_ACTIONABLE_VERDICTS = frozenset({"BLOCK", "WARN"})


class LessonPromoter:
    """Aggregates verdict episodes and promotes recurring patterns to L1."""

    def __init__(
        self,
        *,
        episodic_repo: EpisodicRepo,
        longterm_repo: LongtermRepo,
        min_occurrences: int = 3,
    ) -> None:
        if min_occurrences < 1:
            raise ValueError("min_occurrences must be >= 1")
        self._ep = episodic_repo
        self._lt = longterm_repo
        self._min = min_occurrences

    async def promote(self, engagement_id: str) -> int:
        """Run one promotion pass for the engagement.

        Returns the number of **new** promoted-lesson rows inserted into the
        long-term store. Updates to existing rows (occurrence count rising)
        do not count as new promotions.
        """
        episodes = self._ep.list_episodes_for_engagement(engagement_id)
        if not episodes:
            return 0

        groups: Counter[tuple[str, str]] = Counter()
        last_reason: dict[tuple[str, str], str] = {}
        for episode in episodes:
            key = self._key_for(episode)
            groups[key] += 1
            # Latest reason wins — operator messaging stays fresh as patterns
            # evolve over the engagement.
            last_reason[key] = episode.reason

        new_promotions = 0
        for (command, verdict), count in groups.items():
            if verdict not in _ACTIONABLE_VERDICTS or count < self._min:
                continue
            summary = (
                f"Command '{command}' repeatedly produced {verdict} "
                f"({count} occurrences). Last reason: {last_reason[(command, verdict)]}"
            )
            existing = self._lt.find_by_command(command, verdict)
            self._lt.upsert_lesson(
                PromotedLesson(
                    id=None,
                    command=command,
                    verdict=verdict,
                    summary=summary,
                    occurrence_count=count,
                    created_at=datetime.now(),
                )
            )
            if existing is None:
                new_promotions += 1
                logger.info(
                    "lesson promoted: command=%s verdict=%s occurrences=%d",
                    command,
                    verdict,
                    count,
                )
        return new_promotions

    @staticmethod
    def _key_for(episode: Episode) -> tuple[str, str]:
        # Normalize so case/whitespace variants of the same command collapse
        # into a single pattern.
        return (episode.command.strip().lower(), episode.verdict)


__all__ = ["LessonPromoter"]
