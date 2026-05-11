"""redrobin/infrastructure/llm/anthropic_client.py — Anthropic LLM client with cache_control.

Per spec §13.3 caching strategy. Single retry layer (SDK only, max_retries=5);
no custom backoff per Task 6-D5 audit finding.

Structure of system blocks sent to Anthropic API:
  [0] stable_system_prompt            → cache_control: ephemeral 1h
                                        (persona + format spec)
  [1] cacheable_kb_chunks              → cache_control: ephemeral 5min
                                        (KB RAG retrieval, per-query)
  [2] cacheable_engagement_summary     → cache_control: ephemeral 5min
                                        (engagement.yaml summary, slice 7;
                                        stable per session, swapped at boot)
  [3] dynamic_state                    → uncached (per-turn working memory)

Empty values collapse their block so the system array stays minimal.

Cache invalidation rules:
  - System prompt: invalidated only on prompt version bump (~never per session)
  - KB chunks: invalidated on engagement switch or KB re-index
  - Engagement summary: invalidated only on engagement switch (~once per boot)
  - Dynamic state: never cached
"""

from __future__ import annotations

import anthropic

from redrobin.domain.llm import LLMProvider, LLMResponse
from redrobin.infrastructure.llm.api_keys import get_anthropic_key


class AnthropicClient(LLMProvider):
    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        summarizer_model: str = "claude-haiku-4-5",
        max_retries: int = 5,
    ):
        self._model = model
        self._summarizer_model = summarizer_model
        # SDK auto-retries on 429/5xx/connection errors with exponential backoff.
        # NO custom backoff layer — single source of truth (per Task 4-6 audit D5).
        self._client = anthropic.AsyncAnthropic(
            api_key=get_anthropic_key(),
            max_retries=max_retries,
        )

    async def complete_with_cache(
        self,
        *,
        stable_system_prompt: str,
        cacheable_kb_chunks: list[str],
        cacheable_engagement_summary: str = "",
        dynamic_state: str,
        user_message: str,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        kb_text = "\n\n".join(cacheable_kb_chunks) if cacheable_kb_chunks else ""

        system_blocks: list[dict] = [
            {
                "type": "text",
                "text": stable_system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        if kb_text:
            system_blocks.append(
                {
                    "type": "text",
                    "text": kb_text,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if cacheable_engagement_summary:
            system_blocks.append(
                {
                    "type": "text",
                    "text": cacheable_engagement_summary,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if dynamic_state:
            system_blocks.append({"type": "text", "text": dynamic_state})

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_message}],
        )

        return LLMResponse(
            text=response.content[0].text,
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0)
            or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            output_tokens=response.usage.output_tokens,
        )
