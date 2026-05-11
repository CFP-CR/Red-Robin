"""redrobin/application/layer_a_engine.py — Deterministic Layer A YAML rule engine.

Per spec §1.2.1 verdict pipeline Stage 2. Target: <100ms p95.

Evaluation order:
  1. If command is in passthrough whitelist → PASS immediately
  2. Walk rules in order; first match wins
  3. Map RuleSeverity → VerdictAction:
       INFO   → PASS (annotated)
       WARN   → WARN
       DANGER → BLOCK
  4. If no rule matches → ESCALATE (Layer B picks up async)
"""

from __future__ import annotations

from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.domain.rules import Rule, RuleSeverity

SEVERITY_TO_ACTION: dict[RuleSeverity, VerdictAction] = {
    RuleSeverity.INFO: VerdictAction.PASS,
    RuleSeverity.WARN: VerdictAction.WARN,
    RuleSeverity.DANGER: VerdictAction.BLOCK,
}


class LayerAEngine:
    def __init__(self, rules: list[Rule], passthrough: set[str]):
        self._rules = rules
        self._passthrough = passthrough

    def evaluate(self, *, command: str, args: str) -> Verdict:
        if command in self._passthrough:
            return Verdict(action=VerdictAction.PASS, reason="passthrough", rule_id=None)
        for rule in self._rules:
            if rule.matches(command, args):
                return Verdict(
                    action=SEVERITY_TO_ACTION[rule.severity],
                    reason=rule.reason,
                    rule_id=rule.id,
                    suggestions=list(rule.suggestions),
                )
        return Verdict(action=VerdictAction.ESCALATE, reason="novel pattern", rule_id=None)

    def update_rules(self, rules: list[Rule]) -> None:
        """Hot-reload callback target. Called by RulesLoader on YAML change."""
        self._rules = rules
