"""redrobin/domain/rules.py — Layer A YAML rule schema (spec §4 Componente 4).

Each rule is loaded from `~/.redrobin/rules/{shared,personal}/*.yaml` via
`infrastructure/rules_loader.py` (Task 15) and consumed by `LayerAEngine`
(Task 16).

Two match shapes are supported:

* Native Red Robin: ``match.command`` + ``match.arg_pattern``. Backwards
  compatible with the original v0.1 rule format.
* Sigma-style predicates: ``match_predicates`` — a tuple of
  :class:`MatchPredicate` evaluated AND-wise against ``command`` and
  ``args``. This is how the Sigma loader expresses ``CommandLine|contains``,
  ``Image|endswith``, etc. while keeping the domain layer ignorant of Sigma
  syntax.

Predicate evaluation precedence:

1. If ``match_predicates`` is non-empty it is the sole criterion; the legacy
   ``RuleMatch.command`` is ignored. This lets the Sigma loader emit rules
   without binding to a single command name.
2. Otherwise the legacy ``RuleMatch`` is evaluated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class RuleSeverity(Enum):
    INFO = "INFO"
    WARN = "WARN"
    DANGER = "DANGER"


@dataclass(frozen=True)
class RuleMatch:
    command: str  # exact command name (e.g., "mimikatz", "shell")
    arg_pattern: str | None  # regex against arg string (case-insensitive); None = match any args


PredicateField = Literal["command", "args"]
PredicateOp = Literal["equals", "contains", "startswith", "endswith"]


@dataclass(frozen=True)
class MatchPredicate:
    """One Sigma-style match clause.

    ``field`` is either ``command`` (the operator-typed command name, e.g.
    ``mimikatz``) or ``args`` (the full raw arg string after the command, e.g.
    ``sekurlsa::logonpasswords``).

    ``op`` is one of ``equals``/``contains``/``startswith``/``endswith``.

    ``values`` is a tuple — when more than one value is provided the
    predicate is OR over the list (matches if any value matches). This
    mirrors Sigma's list-valued field semantics.

    Matching is case-insensitive across the board, consistent with the
    legacy ``arg_pattern`` behavior and how operator-typed commands surface
    on Windows shells.
    """

    field: PredicateField
    op: PredicateOp
    values: tuple[str, ...]

    def evaluate(self, command: str, args: str) -> bool:
        target = command if self.field == "command" else args
        target_lower = target.lower()
        for value in self.values:
            v = value.lower()
            if self.op == "equals" and target_lower == v:
                return True
            if self.op == "contains" and v in target_lower:
                return True
            if self.op == "startswith" and target_lower.startswith(v):
                return True
            if self.op == "endswith" and target_lower.endswith(v):
                return True
        return False


@dataclass(frozen=True)
class Rule:
    id: str
    match: RuleMatch
    severity: RuleSeverity
    reason: str
    suggestions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    # Sigma-style predicates evaluated AND-wise. When non-empty, the legacy
    # ``RuleMatch`` is bypassed entirely. An empty tuple here preserves the
    # original v0.1 single-command matching path.
    match_predicates: tuple[MatchPredicate, ...] = field(default_factory=tuple)

    def matches(self, command: str, args: str) -> bool:
        if self.match_predicates:
            return all(p.evaluate(command, args) for p in self.match_predicates)
        if command != self.match.command:
            return False
        if self.match.arg_pattern is None:
            return True
        return re.search(self.match.arg_pattern, args, re.IGNORECASE) is not None
