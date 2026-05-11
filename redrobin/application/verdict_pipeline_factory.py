"""redrobin/application/verdict_pipeline_factory.py — DI factory for VerdictPipeline.

Lazily constructs the singleton pipeline at first request. Wires:
  - LayerAEngine — loaded with rules from ~/.redrobin/rules/ if dir exists, else empty
  - ScopeEnforcer — empty in_scope/out_of_scope (populated per-engagement in Task 28)
  - LayerBEngine — Anthropic LLM + Voyage embeddings + KB service

The factory accepts overrides for testing (inject mock LLM/KB).
"""

from __future__ import annotations

from functools import lru_cache

from redrobin.application.kb_service import KBService
from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.verdict_pipeline import LayerBProvider, VerdictPipeline
from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.infrastructure.filesystem import REDROBIN_ROOT
from redrobin.infrastructure.llm.anthropic_client import AnthropicClient
from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.rules_loader import RulesLoader
from redrobin.infrastructure.vector_store import VectorStore

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


class _DegradedLayerB:
    """No-op Layer B used when API keys are missing.

    Lets the daemon serve Layer A verdicts even without API keys
    (development/CI workflows). Logged at startup; operator sees the banner in TUI.
    """

    async def evaluate(self, *, command: str, raw: str) -> Verdict:
        return Verdict(
            action=VerdictAction.PASS,
            reason="Layer B degraded (API keys missing) — Path 3 observation only",
            rule_id=None,
        )


def _build_layer_b() -> LayerBProvider:
    """Build real LayerBEngine. Falls back to degraded no-op when API keys missing."""
    try:
        llm = AnthropicClient()
        voyage = VoyageClient()
        kb_index_path = REDROBIN_ROOT / "shared" / "kb-index" / "index.npz"
        store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()
        kb = KBService(voyage=voyage, store=store)
        return LayerBEngine(llm=llm, kb=kb)
    except RuntimeError:
        # API keys missing — fall back to a no-op Layer B so daemon can still serve
        # Layer A verdicts. Logged at startup; operator sees the banner in TUI.
        return _DegradedLayerB()


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
    layer_b = _build_layer_b()
    return VerdictPipeline(scope=scope, layer_a=layer_a, layer_b=layer_b)


def reset_pipeline_cache() -> None:
    """Reset the lru_cache — used by tests for isolation."""
    get_verdict_pipeline.cache_clear()
