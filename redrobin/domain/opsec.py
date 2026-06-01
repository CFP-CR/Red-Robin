"""redrobin/domain/opsec.py — OPSEC verdict types.

Per spec §1.2.1 two-tier verdict protocol + §4 Componente 4. Verdict surfaces:
  - PASS:     command proceeds; no operator action
  - WARN:     command proceeds; soft annotation
  - BLOCK:    command halted in alias body; operator must override explicitly
  - ESCALATE: Layer A had no match; alias body forwards (fail-open) and Layer B
              runs async for retroactive verdict (see spec §1.2.1)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerdictAction(Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class Verdict:
    action: VerdictAction
    reason: str
    rule_id: str | None
    suggestions: list[str] = field(default_factory=list)

    @property
    def is_block(self) -> bool:
        return self.action == VerdictAction.BLOCK

    @property
    def is_warn(self) -> bool:
        return self.action == VerdictAction.WARN

    @property
    def requires_layer_b(self) -> bool:
        return self.action == VerdictAction.ESCALATE
