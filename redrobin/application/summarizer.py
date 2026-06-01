"""redrobin/application/summarizer.py — L5 sliding-window summariser.

Closes Phase 2 gap #18 (L5 sliding-window summary never created).

Periodically reads recent ``Episode`` rows from :class:`EpisodicRepo`,
compresses them into a 3-5 sentence summary via an LLM, and appends the
result to :class:`ContextSummaryRepo`. The latest summary is consumed by
:class:`~redrobin.application.layer_b_engine.LayerBEngine` and forwarded to
the LLM as a separate cached system block so long engagements stay coherent
after the most recent verdicts have rolled off the in-memory window.

The summariser tracks the last summarised ``Episode.id`` in-process so a
second pass with no new episodes is a no-op. Window boundaries are crossed
when ``len(episodes_since_last) >= window_size`` — the summariser does not
fire mid-window, so an engagement that idles between windows produces no
extra rows.

LLM interface
-------------
The summariser depends on the minimal protocol
``L5LLM.summarize(*, episodes_text: str) -> str``. The production code wires
this to :class:`AnthropicSummarizerClient`, which issues a single uncached
``messages.create`` call against Anthropic's cheap summarizer model. Tests
inject a plain async stub matching the same shape — no LLM SDK dependency.
"""

from __future__ import annotations

import logging
from typing import Protocol

from redrobin.domain.memory import ContextSummary, Episode
from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo

logger = logging.getLogger(__name__)


class L5LLM(Protocol):
    """Narrow LLM contract used by the summariser.

    The full :class:`LLMProvider` is overkill here: the summariser issues an
    uncached one-shot prompt with no KB context, no engagement summary, and
    no working memory. Carving out this protocol keeps the summariser
    decoupled from the cache-control plumbing in
    ``complete_with_cache(...)`` and matches Phase 2 spec's "Haiku-class
    summariser" guidance (cheap model, single call).
    """

    async def summarize(self, *, episodes_text: str) -> str: ...


_DEFAULT_PROMPT_HEADER = (
    "Compress the following Red Robin episode log into a 3-5 sentence "
    "operational summary. Emphasise recurring (command, verdict) patterns, "
    "blocked tradecraft, and any escalation signals. Stay factual; do not "
    "speculate about engagement intent.\n\n"
)


class L5Summarizer:
    """Compress recent ``Episode`` rows into a single ``ContextSummary``."""

    def __init__(
        self,
        *,
        episodic_repo: EpisodicRepo,
        summary_repo: ContextSummaryRepo,
        llm: L5LLM | None,
        engagement_id: str,
        window_size: int = 25,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self._ep = episodic_repo
        self._cs = summary_repo
        self._llm = llm
        self._engagement_id = engagement_id
        self._window = window_size
        # High-water mark of summarised Episode.id. Episodes with id strictly
        # greater than this contribute to the next window. Initial value of
        # 0 is intentional — sqlite AUTOINCREMENT issues ids starting at 1.
        self._last_summarised_id: int = 0

    async def run_once(self) -> bool:
        """Run one compression pass.

        Returns ``True`` when a new summary row was persisted, ``False`` when
        the window threshold has not been reached or the LLM is missing.
        """
        episodes = self._ep.list_episodes_for_engagement(self._engagement_id)
        pending = [e for e in episodes if (e.id or 0) > self._last_summarised_id]
        if len(pending) < self._window:
            return False
        if self._llm is None:
            logger.warning("L5Summarizer: window threshold met but no LLM configured — skipping")
            return False

        # Take the oldest ``window_size`` pending episodes. Anything after
        # the window is left for the next pass so summaries stay aligned
        # with their input window.
        batch = pending[: self._window]
        episodes_text = self._format(batch)
        try:
            text = await self._llm.summarize(episodes_text=episodes_text)
        except Exception:
            logger.exception("L5Summarizer: LLM call failed; keeping window pending")
            return False

        self._cs.append(
            ContextSummary(
                engagement_id=self._engagement_id,
                text=text,
                window_size=self._window,
            )
        )
        # Advance the high-water mark only after a successful append so a
        # failed write does not silently drop the window.
        self._last_summarised_id = max((e.id or 0) for e in batch)
        logger.info(
            "L5Summarizer: emitted summary engagement=%s window=%d high_water=%d",
            self._engagement_id,
            self._window,
            self._last_summarised_id,
        )
        return True

    @staticmethod
    def _format(episodes: list[Episode]) -> str:
        lines = [
            f"- {e.created_at.isoformat()} [{e.verdict}] {e.command} :: {e.reason}".rstrip(" ::")
            for e in episodes
        ]
        return _DEFAULT_PROMPT_HEADER + "\n".join(lines)


__all__ = ["L5Summarizer", "L5LLM"]
