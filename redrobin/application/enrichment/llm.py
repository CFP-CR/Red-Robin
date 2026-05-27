"""Narrow LLM contract for the enricher + a degraded-aware factory.

Mirrors the L5 summarizer's L5LLM/build_summarizer_llm pattern: a structural
Protocol the enricher depends on, and a factory that returns the Anthropic-backed
client when ANTHROPIC_API_KEY is present, else None (enrichment runs degraded —
the enricher short-circuits when llm is None, daemon still boots).
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class EnricherLLM(Protocol):
    """Single-call extraction: command output -> JSON deltas (as a string)."""

    async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str: ...


def build_enricher_llm(*, model: str = "claude-haiku-4-5") -> EnricherLLM | None:
    try:
        from redrobin.infrastructure.llm.anthropic_client import AnthropicEnricherClient

        return AnthropicEnricherClient(model=model)
    except RuntimeError:
        logger.info("WM enricher: ANTHROPIC_API_KEY missing — running degraded (no enrichment)")
        return None
