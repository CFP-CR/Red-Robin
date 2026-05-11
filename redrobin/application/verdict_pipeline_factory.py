"""redrobin/application/verdict_pipeline_factory.py — DI factory for VerdictPipeline.

Slice 5 replaces the ``lru_cache``'d singleton with a bus-aware non-cached
builder. The pipeline is now created once in ``redrobin.api.app.lifespan`` and
stored on ``app.state.verdict_pipeline``; the route handler reads it from
``request.app.state``. Caching at the factory level is incompatible with the
EventBus being a per-process dependency that lives on the FastAPI app.

Helpers:
  - :func:`build_pipeline` — production builder. Wires the layer-b retroactive
    hook back to ``pipeline._on_retroactive`` so any caller driving Layer B
    outside the pipeline still surfaces the retroactive correction on the bus.
  - :func:`build_pipeline_for_test` — minimal pipeline for unit tests: an
    ALLOW-everything Layer A, no Layer B. Lets emission tests focus on the
    bus contract without needing real LLM/Voyage clients.
  - :func:`build_default_dependencies` — constructs the real LayerA/LayerB
    stack (rules dir, API clients, KB store). Called by the lifespan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from redrobin.application.event_bus import EventBus
from redrobin.application.kb_service import KBService
from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.layer_b_engine import LayerBEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.application.verdict_pipeline import LayerBProvider, VerdictPipeline
from redrobin.domain.opsec import Verdict, VerdictAction
from redrobin.domain.rules import Rule
from redrobin.infrastructure.filesystem import REDROBIN_ROOT
from redrobin.infrastructure.llm.anthropic_client import AnthropicClient
from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.persistence.context_summary_repo import (
    ContextSummaryRepo,
)
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

    # Slice 5 — kept structurally compatible with :class:`LayerBEngine` so the
    # factory can wire ``on_retroactive_verdict`` onto whichever Layer B
    # instance ends up in the pipeline. The pipeline never reads from this
    # attribute on the degraded stub, but binding it keeps types consistent.
    on_retroactive_verdict: object = None

    async def evaluate(self, *, command: str, raw: str) -> Verdict:
        return Verdict(
            action=VerdictAction.PASS,
            reason="Layer B degraded (API keys missing) — Path 3 observation only",
            rule_id=None,
        )


def _build_layer_b(
    *,
    summary_repo: ContextSummaryRepo | None = None,
    engagement_id_provider: Callable[[], str] | None = None,
) -> LayerBProvider:
    """Build real LayerBEngine. Falls back to degraded no-op when API keys missing.

    Slice 13 threads the L5 sliding-window summary repo + engagement-id
    provider into the engine so :meth:`LayerBEngine.evaluate` can read the
    latest summary and inject it as a cached system block. Both are optional
    so callers that don't wire the summariser (older tests, the degraded
    path) keep working unchanged.
    """
    try:
        llm = AnthropicClient()
        voyage = VoyageClient()
        kb_index_path = REDROBIN_ROOT / "shared" / "kb-index" / "index.npz"
        store = VectorStore.load(kb_index_path) if kb_index_path.exists() else VectorStore()
        kb = KBService(voyage=voyage, store=store)
        return LayerBEngine(
            llm=llm,
            kb=kb,
            summary_repo=summary_repo,
            engagement_id_provider=engagement_id_provider,
        )
    except RuntimeError:
        # API keys missing — fall back to a no-op Layer B so daemon can still serve
        # Layer A verdicts. Logged at startup; operator sees the banner in TUI.
        return _DegradedLayerB()


@dataclass(frozen=True)
class PipelineDependencies:
    """Concrete dependencies for :func:`build_pipeline`.

    Carved out so the lifespan can build dependencies once at startup and the
    same wiring can be reused by tests that want a real stack with overrides.
    """

    scope: ScopeEnforcer
    layer_a: LayerAEngine
    layer_b: LayerBProvider


def build_default_dependencies(
    rules: list[Rule] | None = None,
    *,
    summary_repo: ContextSummaryRepo | None = None,
    engagement_id_provider: Callable[[], str] | None = None,
) -> PipelineDependencies:
    """Resolve the real LayerA/LayerB stack from disk + API keys.

    Used by the daemon lifespan. ``rules`` is optional: if the caller already
    loaded the YAML rules from ``REDROBIN_RULES_DIR`` (the lifespan does this
    so the hot-reload watcher has a handle on the loader), it should pass them
    in to avoid double-loading. When ``None``, the factory falls back to
    ``REDROBIN_ROOT/rules`` for backward compatibility.

    Slice 13 adds ``summary_repo`` + ``engagement_id_provider`` so the
    lifespan can thread the L5 sliding-window summary repo into Layer B at
    construction time. Both default to ``None`` so test callers that don't
    care about the summariser stay unaffected.

    The scope is wildcard (empty out_of_scope) until engagement-aware scope
    wiring lands in Slice 6.
    """
    if rules is None:
        rules_dir = REDROBIN_ROOT / "rules"
        if rules_dir.exists():
            loader = RulesLoader(rule_dir=rules_dir)
            rules = loader.load_all()
        else:
            rules = []
    layer_a = LayerAEngine(rules=rules, passthrough=set(DEFAULT_PASSTHROUGH))
    scope = ScopeEnforcer(in_scope=[], out_of_scope=[])
    layer_b = _build_layer_b(
        summary_repo=summary_repo,
        engagement_id_provider=engagement_id_provider,
    )
    return PipelineDependencies(scope=scope, layer_a=layer_a, layer_b=layer_b)


def build_pipeline(
    *,
    event_bus: EventBus,
    scope: ScopeEnforcer,
    layer_a: LayerAEngine,
    layer_b: LayerBProvider,
) -> VerdictPipeline:
    """Build a bus-aware pipeline and wire the Layer B retroactive callback.

    The retroactive hook on Layer B is bound to ``pipeline._on_retroactive``
    so external Layer B implementations can drive the bus emission path
    without going through ``_run_layer_b``. In Phase 1 the pipeline still
    orchestrates Layer B itself, so the binding is a forward-compat seam.
    """
    pipeline = VerdictPipeline(
        scope=scope,
        layer_a=layer_a,
        layer_b=layer_b,
        event_bus=event_bus,
    )
    # Wire the hook on whichever Layer B implementation we got — real
    # ``LayerBEngine`` always has the attribute; the degraded stub above
    # also exposes it for type consistency.
    if hasattr(layer_b, "on_retroactive_verdict"):
        layer_b.on_retroactive_verdict = pipeline._on_retroactive
    return pipeline


def reset_pipeline_cache() -> None:
    """Deprecated no-op kept for tests carried over from the pre-Slice-5 design.

    The factory no longer caches the pipeline — :func:`build_pipeline` is the
    sole entry point and is called once per FastAPI app from the lifespan. Test
    isolation is achieved by patching ``REDROBIN_ROOT`` and entering the
    TestClient as a context manager so the lifespan re-runs for every test.
    """
    return None


def build_pipeline_for_test(*, event_bus: EventBus) -> VerdictPipeline:
    """Minimal pipeline for unit tests: an ALLOW-everything Layer A, no Layer B.

    Layer A is configured with a passthrough whitelist that covers every
    command the integration tests send, so every quick_verdict returns PASS
    without needing rules on disk. Layer B is a no-op stub — the emission tests
    don't trigger Layer B; they call ``pipeline._on_retroactive`` directly.
    """

    class _AllowAllLayerB:
        on_retroactive_verdict: object = None

        async def evaluate(self, *, command: str, raw: str) -> Verdict:
            return Verdict(action=VerdictAction.PASS, reason="test", rule_id=None)

    # An empty rules list + a passthrough that ALSO covers wildcard means every
    # command short-circuits to PASS. ``LayerAEngine`` checks passthrough as a
    # set membership lookup, so we list every command the emission tests send.
    layer_a = LayerAEngine(
        rules=[],
        passthrough={"pwd", "cd", "ls", "mimikatz", "ps", "getuid"},
    )
    scope = ScopeEnforcer(in_scope=["*"], out_of_scope=[])
    return build_pipeline(
        event_bus=event_bus,
        scope=scope,
        layer_a=layer_a,
        layer_b=_AllowAllLayerB(),
    )
