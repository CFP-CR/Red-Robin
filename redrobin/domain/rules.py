"""redrobin/domain/rules.py — Layer A YAML rule schema (spec §4 Componente 4).

Each rule is loaded from `~/.redrobin/rules/{shared,personal}/*.yaml` via
`infrastructure/rules_loader.py` (Task 15) and consumed by `LayerAEngine`
(Task 16).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class RuleSeverity(Enum):
    INFO = "INFO"
    WARN = "WARN"
    DANGER = "DANGER"


@dataclass(frozen=True)
class RuleMatch:
    command: str  # exact command name (e.g., "mimikatz", "shell")
    arg_pattern: str | None  # regex against arg string (case-insensitive); None = match any args


@dataclass(frozen=True)
class Rule:
    id: str
    match: RuleMatch
    severity: RuleSeverity
    reason: str
    suggestions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def matches(self, command: str, args: str) -> bool:
        if command != self.match.command:
            return False
        if self.match.arg_pattern is None:
            return True
        return re.search(self.match.arg_pattern, args, re.IGNORECASE) is not None
