"""redrobin/application/layer_b_engine.py — Layer B LLM judge with RAG.

Per spec §1.2.1 Stage 3 + §4 Componente 4 + §13.3 cache_control:
- System prompt: stable persona + JSON format spec (1h cache TTL)
- KB chunks: RAG context retrieved per query (5min cache TTL, default ephemeral)
- Engagement summary: per-engagement OPSEC posture from engagement.yaml,
  set once at boot via :meth:`LayerBEngine.set_engagement_summary` and surfaced
  as a labeled ``# Engagement Context`` block (5min cache TTL — slice 7).
- Dynamic state: per-turn working memory (uncached) — reserved for future use.

Returns a Verdict by parsing the LLM's JSON response. On parse failure,
returns ESCALATE/PASS (fail-open per §1.2.1 safety net design).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from typing import Awaitable, Callable

from redrobin.application.kb_service import KBService
from redrobin.domain.llm import LLMProvider
from redrobin.domain.opsec import Verdict, VerdictAction

logger = logging.getLogger(__name__)


# Retroactive callback wired by VerdictPipeline (Slice 5). The pipeline itself
# orchestrates Layer B today; the attribute exists so future callers can drive
# the retroactive emission path without going through the pipeline. Kwargs form
# matches ``VerdictPipeline._on_retroactive``.
RetroactiveVerdictHook = Callable[..., Awaitable[None]]


SYSTEM_PROMPT = """\
You are Red Robin, an OPSEC advisor for a red team operator using Cobalt Strike.

Given the operator's proposed Beacon command, the engagement state, and retrieved
relevant knowledge base chunks, decide whether this command should PASS, WARN, or
BLOCK. Consider:
- Detection signatures (EDR/Sysmon/auditd) the command is known to trigger
- Engagement context: current phase, objectives, defenses observed, kill chain
- Whether KB chunks provide grounded reasoning (cite when relevant)

Respond with ONLY a JSON code block in this exact format:

```json
{
  "action": "PASS" | "WARN" | "BLOCK",
  "reason": "1-2 sentence rationale citing engagement state and/or detection prior",
  "suggestions": ["alternative 1", "alternative 2"]
}
```

If no useful suggestions exist, return an empty list. Use BLOCK sparingly — only
when detection is near-certain or ROE is at risk.
"""


@dataclass(frozen=True)
class EngagementContext:
    """Dynamic engagement state passed to Layer B per call (uncached)."""

    summary: str  # text representation of working memory (hosts/accounts/defenses/etc.)


class LayerBEngine:
    """LLM judge with RAG context retrieval."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        kb: KBService,
        top_k_kb_chunks: int = 5,
        context_provider: type[EngagementContext] = EngagementContext,
    ):
        self._llm = llm
        self._kb = kb
        self._top_k = top_k_kb_chunks
        self._context_cls = context_provider
        self._engagement_summary = ""  # set per-engagement by daemon (Task 28+)
        # Retroactive hook — bound by the factory to
        # ``VerdictPipeline._on_retroactive`` so callers that drive Layer B
        # outside the pipeline still surface retroactive verdicts on the bus.
        # Today the pipeline orchestrates Layer B itself, so this attribute is
        # set-but-unused unless someone bypasses the pipeline.
        self.on_retroactive_verdict: RetroactiveVerdictHook | None = None

    def set_engagement_summary(self, summary: str) -> None:
        """Called when active engagement changes; rebuilds dynamic state block."""
        self._engagement_summary = summary

    async def evaluate(self, *, command: str, raw: str) -> Verdict:
        """Retrieve KB context, call LLM, parse JSON verdict."""
        try:
            kb_hits = await self._kb.retrieve(raw, top_k=self._top_k)
        except Exception:
            logger.exception("KB retrieval failed; proceeding without RAG context")
            kb_hits = []

        kb_chunks = [text for text, _source, _score in kb_hits]
        # Slice 7 — surface the engagement.yaml summary as a *cached* system
        # block so every retroactive verdict in the session amortizes the
        # engagement-specific OPSEC posture (low-detection priority, scope
        # nuances, prior tradecraft constraints). The block is stable across
        # the session — only invalidated on engagement switch — so the 5min
        # ephemeral cache pays for itself across a single operator workflow.
        engagement_block = (
            f"# Engagement Context\n{self._engagement_summary}"
            if self._engagement_summary
            else ""
        )
        # Reserved for future per-turn working memory (e.g., hosts seen this
        # tick). Today the daemon does not feed working memory into Layer B,
        # so dynamic_state stays empty and Anthropic skips the block entirely.
        dynamic_state = ""
        user_message = (
            f"Proposed command: {command}\n"
            f"Raw input: {raw}\n\n"
            "Decide: PASS / WARN / BLOCK with rationale + suggestions."
        )

        try:
            response = await self._llm.complete_with_cache(
                stable_system_prompt=SYSTEM_PROMPT,
                cacheable_kb_chunks=kb_chunks,
                cacheable_engagement_summary=engagement_block,
                dynamic_state=dynamic_state,
                user_message=user_message,
            )
        except Exception:
            logger.exception("LLM call failed in Layer B; returning PASS (fail-open)")
            return Verdict(
                action=VerdictAction.PASS,
                reason="Layer B unavailable; falling back to Path 3 observation",
                rule_id=None,
            )

        return self._parse_verdict(response.text)

    @staticmethod
    def _parse_verdict(text: str) -> Verdict:
        """Extract JSON block from LLM response and map to Verdict."""
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match is None:
            # Fallback: try parsing whole response as JSON
            blob = text.strip()
        else:
            blob = json_match.group(1)
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            logger.warning("Layer B response not parseable JSON; returning PASS")
            return Verdict(
                action=VerdictAction.PASS,
                reason="Layer B returned unparseable response (fail-open)",
                rule_id=None,
            )

        action_str = str(payload.get("action", "PASS")).upper()
        try:
            action = {
                "PASS": VerdictAction.PASS,
                "WARN": VerdictAction.WARN,
                "BLOCK": VerdictAction.BLOCK,
            }[action_str]
        except KeyError:
            logger.warning("Layer B returned unknown action %r; returning PASS", action_str)
            action = VerdictAction.PASS

        return Verdict(
            action=action,
            reason=payload.get("reason", ""),
            rule_id=None,  # Layer B verdicts have no rule_id; rule_id is Layer A only
            suggestions=list(payload.get("suggestions", [])),
        )
