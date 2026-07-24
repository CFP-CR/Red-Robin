"""redrobin/application/longterm_lifecycle.py — facade for L1/L3/L5 repos at daemon boot.

Re-exports the infrastructure ``EpisodicRepo`` + ``LongtermRepo`` +
``ContextSummaryRepo`` so the api layer can wire them in lifespan without
violating ``no-direct-infra-from-presentation``. Mirrors
``working_memory_lifecycle``.

Slice 13 adds ``ContextSummaryRepo`` (L5 sliding-window summary store) and
:func:`build_summarizer_llm`, a small helper that returns an
``AnthropicSummarizerClient`` when API keys are available and ``None``
otherwise — so the api layer can schedule the L5 summariser loop without
importing infrastructure directly.
"""

from __future__ import annotations

import logging

from redrobin.application.agents.summarizer import L5LLM
from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo

logger = logging.getLogger(__name__)


def build_summarizer_llm() -> L5LLM | None:
    """Construct an ``AnthropicSummarizerClient`` when keys are present.

    Returns ``None`` when ``ANTHROPIC_API_KEY`` is missing so the lifespan
    can still schedule the L5 summariser loop in a degraded posture: the
    loop runs, but every call short-circuits because ``L5Summarizer`` skips
    when the LLM is ``None``. Mirrors the degraded Layer B path from Slice
    5 so dev/CI workflows without keys boot cleanly.
    """
    try:
        from redrobin.infrastructure.llm.anthropic_client import (
            AnthropicSummarizerClient,
        )

        return AnthropicSummarizerClient()
    except RuntimeError:
        logger.info(
            "L5 summariser: ANTHROPIC_API_KEY missing — running in degraded "
            "mode (no compressions written)"
        )
        return None


__all__ = [
    "ContextSummaryRepo",
    "EpisodicRepo",
    "LongtermRepo",
    "build_summarizer_llm",
]
