"""redrobin/domain/assistant.py — pure AI-assistant types.

`Recommendation` is the 5-mandatory-part advisory output (spec §6.C): the WHAT,
the tradecraft WHY, the OPSEC detection risk (reusing `Severity`), a calibrated
confidence, and the grounding references. `to_payload` serializes it to a
JSON-native dict for the `advisor.recommendation` bus event (mirrors
`WorkingMemoryDeltas.to_payload`).

`AgentTool` is the provider-agnostic tool spec the assistant hands to the
`AgentLLM` each cycle; the Anthropic Tool Runner adapts it (see
`infrastructure/llm/anthropic_client.py`). Pure types only — no infra imports.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from redrobin.domain.events import Severity

# A minted operator-question id ALWAYS carries this prefix; an accepted milestone_id
# NEVER may (parse_interpretation drops any that does). This makes the operator-question
# and milestone id namespaces provably disjoint, so a plain operator question can never
# collide with a pending milestone and silently commit/drop it (#28 whole-branch review #1).
OPERATOR_QUESTION_ID_PREFIX = "opq-"


@dataclass(frozen=True)
class Recommendation:
    """An advisory next-step (proposes, never acts). Every part embodies the
    thesis: advice with the WHY, not a command."""

    suggested_action: str
    rationale: str
    opsec_risk: Severity
    confidence: str
    references: list[str] = field(default_factory=list)
    correlation_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "suggested_action": self.suggested_action,
            "rationale": self.rationale,
            "opsec_risk": self.opsec_risk.value,
            "confidence": self.confidence,
            "references": list(self.references),
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True)
class Interpretation:
    """The assistant's read of the shared engagement picture (spec §6.C): a phase
    nudge / objective status+progress / kill-chain read. ``needs_confirm`` gates the
    tier — False = auto-write (soft/reversible), True = propose-and-confirm (close/
    commit). ``prompt`` is the binary-confirm question shown for the True tier."""

    phase: str
    milestone_id: str
    status: str
    progress_pct: int
    needs_confirm: bool
    prompt: str


@dataclass(frozen=True)
class AgentTool:
    """A single pull-tool the agent may call during a cycle. Provider-agnostic:
    ``handler`` takes the parsed tool arguments and returns a text result the
    model reads. The Anthropic client adapts this into a Tool Runner tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


__all__ = ["Recommendation", "Interpretation", "AgentTool", "OPERATOR_QUESTION_ID_PREFIX"]
