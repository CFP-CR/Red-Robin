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

Slice 11 — ``evaluate`` accepts optional Windows process metadata
(``integrity_level``, ``original_filename``, ``user``, ``current_directory``,
``description``) that the Sigma matcher uses to constrain detections.
Existing callers that only pass ``command`` + ``args`` work unchanged.
"""

from __future__ import annotations

from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.domain.rules import EventContext, Rule, RuleSeverity

SEVERITY_TO_ACTION: dict[RuleSeverity, VerdictAction] = {
    RuleSeverity.INFO: VerdictAction.PASS,
    RuleSeverity.WARN: VerdictAction.WARN,
    RuleSeverity.DANGER: VerdictAction.BLOCK,
}


class LayerAEngine:
    def __init__(self, rules: list[Rule], passthrough: set[str]):
        self._rules = rules
        self._passthrough = passthrough

    def evaluate(
        self,
        *,
        command: str,
        args: str,
        integrity_level: str | None = None,
        original_filename: str | None = None,
        user: str | None = None,
        current_directory: str | None = None,
        description: str | None = None,
    ) -> Verdict:
        if command in self._passthrough:
            return Verdict(action=VerdictAction.PASS, reason="passthrough", rule_id=None)
        ctx = EventContext(
            command=command,
            args=args,
            integrity_level=integrity_level,
            original_filename=original_filename,
            user=user,
            current_directory=current_directory,
            description=description,
        )
        for rule in self._rules:
            if rule.matches(ctx):
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
