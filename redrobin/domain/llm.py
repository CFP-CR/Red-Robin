"""redrobin/domain/llm.py — LLMProvider ABC.

The ONE explicit abstraction in Red Robin (spec §9 closed decision 12).
Justified by multi-impl: Anthropic v0.1 cloud, Ollama v0.2 local roadmap.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    """LLM completion result + cache-usage telemetry."""

    text: str
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(ABC):
    """Abstract LLM provider. Implementations: AnthropicClient (v0.1), OllamaClient (v0.2)."""

    @abstractmethod
    async def complete_with_cache(
        self,
        *,
        stable_system_prompt: str,
        cacheable_kb_chunks: list[str],
        cacheable_engagement_summary: str = "",
        cacheable_recent_summary: str = "",
        dynamic_state: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Send structured prompt with cache_control blocks.

        Block layout (Anthropic ``system`` array):
          1. Stable prompt (persona + format spec) → 1h cache.
          2. KB chunks (RAG retrieval, per-query) → 5min cache.
          3. Engagement summary (stable per session, slice 7) → 5min cache.
          4. Recent summary (sliding-window L5 compression, slice 13) → 5min cache.
          5. Dynamic state (per-turn working memory) → uncached.

        Slice 13 splits the L5 sliding-window summary into its own block so
        cache invalidation stays orthogonal: the engagement summary changes
        rarely (engagement switch) while the recent summary changes every
        ~25 commands (window boundary).

        Empty strings / empty lists collapse their block away so the system
        array stays minimal.
        """
