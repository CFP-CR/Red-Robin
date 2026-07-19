"""Narrow LLM contract for the assistant + a degraded-aware factory.

Mirrors the enricher's ``EnricherLLM`` / ``build_enricher_llm``: a structural
Protocol the assistant depends on, and a factory returning the Anthropic-backed
Tool Runner client when ANTHROPIC_API_KEY is present, else None (the assistant
short-circuits when llm is None, daemon still boots). The Tool Runner beta API
is insulated inside ``AnthropicAgentClient`` behind this Protocol.
"""

from __future__ import annotations

import logging
from typing import Protocol

from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)


class AgentLLM(Protocol):
    """Run ONE agentic tool-calling cycle: a system prompt + a user message + a
    set of tools -> the final assistant message as text (a JSON object the
    assistant parses into a Recommendation). The tool-loop (call tool -> feed
    result -> repeat until done) happens inside the impl, insulated here."""

    async def run(
        self,
        *,
        system: str,
        user_message: str,
        tools: list[AgentTool],
        max_tokens: int = 2048,
    ) -> str: ...


def build_agent_llm(*, model: str = "claude-haiku-4-5") -> AgentLLM | None:
    try:
        from redrobin.infrastructure.llm.anthropic_client import AnthropicAgentClient

        return AnthropicAgentClient(model=model)
    except RuntimeError:
        logger.info("assistant: ANTHROPIC_API_KEY missing — running degraded (no recommendations)")
        return None
