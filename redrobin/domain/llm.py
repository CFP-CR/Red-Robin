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
        dynamic_state: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Send structured prompt with cache_control blocks.

        Stable prompt → 1h cache. KB chunks → 5min cache. Dynamic state → uncached.
        """
