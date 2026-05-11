"""redrobin/application/verdict_pipeline.py — 4-stage verdict pipeline.

Per spec §1.2.1 two-tier verdict protocol:
  Stage 0: passthrough whitelist (handled inside Layer A)
  Stage 1: ROE scope enforcement → BLOCK if target out-of-scope
  Stage 2: Layer A YAML rules (sync, <100ms)
  Stage 3: Layer B LLM judge (async retroactive — fires only on ESCALATE)

`quick_verdict()` returns within Stage 2 latency budget (<100ms p95 per spec).
On ESCALATE, Layer B runs as a fire-and-forget asyncio task; retroactive
verdicts (WARN/BLOCK) are surfaced via the optional `on_retroactive_verdict`
callback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Protocol

from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.domain.opsec import Verdict, VerdictAction

logger = logging.getLogger(__name__)


class LayerBProvider(Protocol):
    """Protocol for Layer B engine. Implemented by LayerBEngine (Task 26)."""

    async def evaluate(self, *, command: str, raw: str) -> Verdict: ...


RetroactiveCallback = Callable[[Verdict], Awaitable[None]]


class VerdictPipeline:
    def __init__(
        self,
        *,
        scope: ScopeEnforcer,
        layer_a: LayerAEngine,
        layer_b: LayerBProvider,
        on_retroactive_verdict: RetroactiveCallback | None = None,
    ):
        self._scope = scope
        self._layer_a = layer_a
        self._layer_b = layer_b
        self._on_retroactive = on_retroactive_verdict

    async def quick_verdict(self, *, command: str, raw: str, source_event_id: str = "") -> Verdict:
        """Sync stages 0-2. Returns within <100ms p95.

        On ESCALATE: schedules Layer B async; caller proceeds with the ESCALATE
        verdict (fail-open per spec §1.2.1). Retroactive verdict posted via
        `on_retroactive_verdict` callback if Layer B returns WARN or BLOCK.
        """
        # Stage 1: ROE scope check
        targets = self._scope.extract_targets(command=command, raw=raw)
        violation = self._scope.check(targets)
        if violation is not None:
            return Verdict(action=VerdictAction.BLOCK, reason=violation, rule_id="roe")

        # Stage 0 (passthrough inside Layer A) + Stage 2 (rules)
        args = self._strip_command(command, raw)
        verdict = self._layer_a.evaluate(command=command, args=args)

        if verdict.action == VerdictAction.ESCALATE:
            # Fire-and-forget Layer B (Stage 3)
            asyncio.create_task(
                self._run_layer_b(command=command, raw=raw, source_event_id=source_event_id)
            )

        return verdict

    async def _run_layer_b(self, *, command: str, raw: str, source_event_id: str) -> None:
        """Run Layer B async. Surface retroactive verdict via callback if non-PASS."""
        try:
            retro = await self._layer_b.evaluate(command=command, raw=raw)
        except Exception:
            logger.exception(
                "Layer B retroactive verdict failed",
                extra={"command": command, "source_event_id": source_event_id},
            )
            return
        if self._on_retroactive is None:
            return
        if retro.action in (VerdictAction.WARN, VerdictAction.BLOCK):
            try:
                await self._on_retroactive(retro)
            except Exception:
                logger.exception(
                    "Retroactive callback failed",
                    extra={"source_event_id": source_event_id},
                )

    @staticmethod
    def _strip_command(command: str, raw: str) -> str:
        """Strip leading command name from raw to give Layer A the arg string."""
        prefix = f"{command} "
        return raw[len(prefix) :] if raw.startswith(prefix) else raw
