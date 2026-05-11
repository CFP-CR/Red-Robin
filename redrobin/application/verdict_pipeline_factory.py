"""redrobin/application/verdict_pipeline_factory.py — DI factory for VerdictPipeline.

Lazily constructs the singleton pipeline at first request. Wires:
  - LayerAEngine — loaded with rules from ~/.redrobin/rules/ if dir exists, else empty
  - ScopeEnforcer — empty in_scope/out_of_scope (populated per-engagement in Task 28)
  - LayerB placeholder (Null impl — replaced by real LayerBEngine in Task 27)

Tests can override the singleton via monkeypatch.
"""

from __future__ import annotations

from functools import lru_cache

from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.verdict_pipeline import LayerBProvider, VerdictPipeline
from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.infrastructure.filesystem import REDROBIN_ROOT
from redrobin.infrastructure.rules_loader import RulesLoader

# Default passthrough whitelist per spec §1.2.1 + bootstrap pack (Task 38)
DEFAULT_PASSTHROUGH = {
    "pwd",
    "cd",
    "ls",
    "ps",
    "getuid",
    "jobs",
    "sleep",
    "mode",
    "note",
    "clear",
    "help",
}


class _NullLayerB:
    """Placeholder until Task 26+27 wire the real LayerBEngine."""

    async def evaluate(self, *, command: str, raw: str) -> Verdict:
        return Verdict(
            action=VerdictAction.PASS,
            reason="layer B not yet wired (Task 26)",
            rule_id=None,
        )


@lru_cache(maxsize=1)
def get_verdict_pipeline() -> VerdictPipeline:
    rules_dir = REDROBIN_ROOT / "rules"
    if rules_dir.exists():
        loader = RulesLoader(rule_dir=rules_dir)
        rules = loader.load_all()
    else:
        rules = []
    layer_a = LayerAEngine(rules=rules, passthrough=set(DEFAULT_PASSTHROUGH))
    scope = ScopeEnforcer(in_scope=[], out_of_scope=[])
    layer_b: LayerBProvider = _NullLayerB()
    return VerdictPipeline(scope=scope, layer_a=layer_a, layer_b=layer_b)


def reset_pipeline_cache() -> None:
    """Reset the lru_cache — used by tests for isolation."""
    get_verdict_pipeline.cache_clear()
