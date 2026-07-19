"""One agent cycle: assemble context -> run the Tool Runner loop -> parse.

The FROZEN system prompt (persona + the 5-part output contract) is sent as the
cached 1h prefix by the AgentLLM; the VOLATILE push-context (L2 working-memory
snapshot + L5 summary + the trigger command) goes in the user message, AFTER the
cached prefix (spec §6.B caching refinement 1: never co-locate the mutating
snapshot with the cached prefix). PUSH = small resident working-set; PULL =
the kb_retrieve tool. This is Anthropic's endorsed hybrid strategy (spec §6.B).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.parser import parse_recommendation
from redrobin.domain.assistant import AgentTool, Recommendation

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are Red Robin, an operation-aware AI copilot behind a red-team operator's C2. "
    "You are ADVISORY ONLY: you propose the next step, you never execute it and never "
    "dispatch to the beacon. You are SELECTIVE — speak only when you have something material "
    "and grounded; otherwise stay silent.\n\n"
    "You are given the operator's current engagement state (working memory) and the story so "
    "far. Use the kb_retrieve tool to ground tradecraft claims in the knowledge base; reason "
    "over what the tools and the state return, never from memory, and never invent a fact.\n\n"
    "Return ONLY a JSON object, no prose, no markdown fences. To stay silent, return "
    '{"speak": false}. To recommend, return these five fields (ALL required):\n'
    '{"suggested_action": "the concrete next step — the WHAT",\n'
    ' "rationale": "the tradecraft WHY given the engagement state (without this it is '
    'outsourcing, not advising)",\n'
    ' "opsec_risk": "one of INFO|WARN|DANGER — the detection risk, grounded in '
    'defenses_observed, with a short reason",\n'
    ' "confidence": "how sure you are AND what you are explicitly NOT sure about",\n'
    ' "references": ["the grounding you drew on — kb sources you retrieved plus the '
    'working-memory facts / command output you used"]}'
)


def build_system_prompt() -> str:
    """The frozen persona + output contract. Static, so it caches as the Tool
    Runner's 1h prefix."""
    return _SYSTEM_PROMPT


def build_push_context(
    *, wm_snapshot: dict[str, Any], l5_summary: str, trigger_command: str
) -> str:
    """Assemble the small, token-bounded PUSH working-set: L2 (current working
    memory) + L5 (story so far) + the triggering command. The volatile block —
    lives in the user message, after the cached system prefix."""
    wm_json = json.dumps(wm_snapshot, ensure_ascii=False, sort_keys=True)
    return "\n".join(
        [
            "# Engagement working memory (L2)",
            wm_json,
            "",
            "# Story so far (L5)",
            l5_summary or "(no summary yet)",
            "",
            "# What just happened",
            f"The operator ran: {trigger_command or '(unknown command)'}",
            "",
            "Given this, is there a material, grounded next-step recommendation? If yes, "
            'retrieve any tradecraft you need and return the 5-part JSON. If not, return {"speak": false}.',
        ]
    )


def state_signature(wm_snapshot: dict[str, Any]) -> str:
    """A cheap, stable signature of engagement state for the trigger's novelty
    check — changes iff the working memory changed."""
    return json.dumps(wm_snapshot, ensure_ascii=False, sort_keys=True)


# The list-valued fact types the enricher extracts — exactly the
# ``WorkingMemoryDeltas.to_payload()`` keys.
_DELTA_LIST_KEYS = ("hosts", "credentials", "findings", "tools_used", "defenses_observed")


def apply_deltas_to_snapshot(
    snapshot: dict[str, Any], deltas_payload: dict[str, Any]
) -> dict[str, Any]:
    """Forward-merge the just-published deltas onto the cached L2 snapshot.

    Production event order is ``working_memory.deltas`` -> ``enrichment.completed``
    -> ``working_memory_snapshot``. The assistant reacts on ``enrichment.completed`` —
    one step BEFORE the authoritative snapshot for that command lands — so on its own
    it would reason over a one-step-stale snapshot missing THIS command's facts.
    Extending the snapshot's list fields with the deltas the assistant already
    received closes that gap. Shallow list-extend only: the authoritative
    ``working_memory_snapshot`` supersedes this within a beat, so we deliberately do
    NOT replicate the reducer's dedup/merge. Returns a NEW dict (and NEW lists) — the
    input ``snapshot`` is never mutated.
    """
    merged = dict(snapshot)
    for key in _DELTA_LIST_KEYS:
        delta_items = deltas_payload.get(key)
        if not delta_items:
            continue
        existing = merged.get(key)
        base = list(existing) if isinstance(existing, list) else []
        merged[key] = base + list(delta_items)
    return merged


async def run_cycle(
    *,
    llm: AgentLLM,
    tools: list[AgentTool],
    wm_snapshot: dict[str, Any],
    l5_summary: str,
    trigger_command: str,
    correlation_id: str | None,
    max_tokens: int = 2048,
) -> Recommendation | None:
    """Run ONE agent cycle: assemble -> Tool Runner loop (via AgentLLM) -> parse
    into a Recommendation, or None to stay silent."""
    raw = await llm.run(
        system=build_system_prompt(),
        user_message=build_push_context(
            wm_snapshot=wm_snapshot, l5_summary=l5_summary, trigger_command=trigger_command
        ),
        tools=tools,
        max_tokens=max_tokens,
    )
    # Intentionally NO retry on unparseable output (unlike the enricher's parse-retry):
    # this is advisory, so silence is the safe default, and the Tool Runner already ran
    # a multi-turn loop — re-asking adds latency, not safety. parse_recommendation
    # returns None and the cycle stays silent. Documented decision (no retry machinery).
    return parse_recommendation(raw, correlation_id=correlation_id)


__all__ = [
    "apply_deltas_to_snapshot",
    "build_system_prompt",
    "build_push_context",
    "state_signature",
    "run_cycle",
]
