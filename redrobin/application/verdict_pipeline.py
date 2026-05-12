"""redrobin/application/verdict_pipeline.py — 4-stage verdict pipeline.

Per spec §1.2.1 two-tier verdict protocol:
  Stage 0: passthrough whitelist (handled inside Layer A)
  Stage 1: ROE scope enforcement → BLOCK if target out-of-scope
  Stage 2: Layer A YAML rules (sync, <100ms)
  Stage 3: Layer B LLM judge (async retroactive — fires only on ESCALATE)

`quick_verdict()` returns within Stage 2 latency budget (<100ms p95 per spec).
On ESCALATE, Layer B runs as a fire-and-forget asyncio task; retroactive
verdicts (WARN/BLOCK) are surfaced via:
  1. ``on_retroactive_verdict`` legacy callback (receives the Verdict) — kept
     so that the existing pipeline unit tests continue to work.
  2. ``_on_retroactive(...)`` async method that publishes an ``opsec.verdict``
     event onto the EventBus with ``source=layer_b`` + ``previous_verdict`` so
     the TUI can correct the original verdict line via ``correlation_id``.

Layer A verdicts also publish to the bus immediately (``source=layer_a``) so
the TUI gets the gate decision concurrent with the HTTP response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Coroutine, Protocol

from redrobin.application.event_bus import EventBus
from redrobin.application.layer_a_engine import LayerAEngine
from redrobin.application.scope_enforcer import ScopeEnforcer
from redrobin.domain.opsec import Verdict, VerdictAction

logger = logging.getLogger(__name__)


class LayerBProvider(Protocol):
    """Protocol for Layer B engine. Implemented by LayerBEngine (Task 26)."""

    async def evaluate(self, *, command: str, raw: str) -> Verdict: ...


RetroactiveCallback = Callable[[Verdict], Awaitable[None]]
TaskFactory = Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]]


def _default_task_factory(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Untracked ``asyncio.create_task`` — leaks on shutdown, used only when
    the pipeline is constructed outside a daemon lifespan (unit tests)."""
    return asyncio.create_task(coro)


class VerdictPipeline:
    def __init__(
        self,
        *,
        scope: ScopeEnforcer,
        layer_a: LayerAEngine,
        layer_b: LayerBProvider,
        event_bus: EventBus | None = None,
        on_retroactive_verdict: RetroactiveCallback | None = None,
        task_factory: TaskFactory | None = None,
    ):
        self._scope = scope
        self._layer_a = layer_a
        self._layer_b = layer_b
        self._bus = event_bus
        # Legacy callback (Verdict → Awaitable). Kept for existing tests that
        # observe the Verdict object directly. Slice 5+ uses ``_on_retroactive``
        # (kwargs form) for bus emission.
        self._on_retroactive_legacy = on_retroactive_verdict
        # Task factory plumbed in from the api lifespan so fire-and-forget
        # Layer B tasks are tracked on ``app.state.background_tasks`` and
        # drained on shutdown. Falls back to a bare ``asyncio.create_task``
        # for unit-test callers that don't run inside a FastAPI lifespan.
        self._task_factory: TaskFactory = task_factory or _default_task_factory

    async def quick_verdict(
        self,
        *,
        command: str,
        raw: str,
        source_event_id: str = "",
        beacon_id: str = "",
        integrity_level: str | None = None,
        original_filename: str | None = None,
        user: str | None = None,
        current_directory: str | None = None,
        description: str | None = None,
    ) -> Verdict:
        """Sync stages 0-2. Returns within <100ms p95.

        Publishes an ``opsec.verdict`` event on the bus (if a bus is wired)
        before returning, so the TUI sees the gate decision via the WS stream
        concurrent with the alias body receiving its HTTP response.

        On ESCALATE: schedules Layer B async; caller proceeds with the ESCALATE
        verdict (fail-open per spec §1.2.1). The retroactive verdict is surfaced
        through ``_on_retroactive`` (bus emission) and the legacy callback.

        Slice 11 — optional Windows process metadata threads through to
        Layer A so Sigma rules can constrain on integrity level / image /
        user / cwd / description. Existing callers that only pass the
        legacy fields keep working (all extras default to ``None``).
        """
        # Stage 1: ROE scope check
        targets = self._scope.extract_targets(command=command, raw=raw)
        violation = self._scope.check(targets)
        if violation is not None:
            verdict = Verdict(action=VerdictAction.BLOCK, reason=violation, rule_id="roe")
        else:
            # Stage 0 (passthrough inside Layer A) + Stage 2 (rules)
            args = self._strip_command(command, raw)
            verdict = self._layer_a.evaluate(
                command=command,
                args=args,
                integrity_level=integrity_level,
                original_filename=original_filename,
                user=user,
                current_directory=current_directory,
                description=description,
            )

        # Emit the Layer A / scope verdict to the bus BEFORE we return to the
        # alias body. The route handler awaits this method, so the bus publish
        # happens within the same request lifecycle — the TUI gets the verdict
        # line via WebSocket concurrent with the operator seeing the HTTP reply.
        await self._publish_layer_a_event(
            verdict=verdict,
            command=command,
            source_event_id=source_event_id,
            beacon_id=beacon_id,
        )

        if verdict.action == VerdictAction.ESCALATE:
            # Fire-and-forget Layer B (Stage 3). Pass the immediate verdict so
            # the retroactive emission can carry it as ``previous_verdict``.
            # The task factory ties this into ``app.state.background_tasks``
            # in production so shutdown awaits / cancels it instead of leaking.
            self._task_factory(
                self._run_layer_b(
                    command=command,
                    raw=raw,
                    source_event_id=source_event_id,
                    original_action=verdict.action,
                )
            )

        return verdict

    async def _run_layer_b(
        self,
        *,
        command: str,
        raw: str,
        source_event_id: str,
        original_action: VerdictAction,
    ) -> None:
        """Run Layer B async. Surface retroactive verdict via callback + bus if non-PASS."""
        try:
            retro = await self._layer_b.evaluate(command=command, raw=raw)
        except Exception:
            logger.exception(
                "Layer B retroactive verdict failed",
                extra={"command": command, "source_event_id": source_event_id},
            )
            return
        if retro.action not in (VerdictAction.WARN, VerdictAction.BLOCK):
            return

        # Legacy Verdict-shaped callback — preserved for Phase 1 unit tests.
        if self._on_retroactive_legacy is not None:
            try:
                await self._on_retroactive_legacy(retro)
            except Exception:
                logger.exception(
                    "Retroactive callback failed",
                    extra={"source_event_id": source_event_id},
                )

        # Bus emission — Slice 5. Publishes the retroactive correction with the
        # ``previous_verdict`` discriminator so the TUI can rewrite the original
        # verdict line by ``correlation_id``.
        await self._on_retroactive(
            correlation_id=source_event_id,
            original_verdict=original_action.value,
            new_verdict=retro.action.value,
            reason=retro.reason,
        )

    async def _on_retroactive(
        self,
        *,
        correlation_id: str,
        original_verdict: str,
        new_verdict: str,
        reason: str,
    ) -> None:
        """Publish an ``opsec.verdict`` event for the Layer B retroactive correction.

        Called by ``_run_layer_b`` once Layer B has produced a retroactive
        verdict, and also bound onto ``LayerBEngine.on_retroactive_verdict`` by
        the factory so external Layer B implementations can drive the same path
        without going through the pipeline orchestration.
        """
        if self._bus is None:
            return
        await self._bus.publish(
            {
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": correlation_id,
                    "verdict": new_verdict,
                    "previous_verdict": original_verdict,
                    "reason": reason,
                    "source": "layer_b",
                },
            }
        )

    async def _publish_layer_a_event(
        self,
        *,
        verdict: Verdict,
        command: str,
        source_event_id: str,
        beacon_id: str,
    ) -> None:
        if self._bus is None:
            return
        payload: dict[str, Any] = {
            "correlation_id": source_event_id,
            "beacon_id": beacon_id,
            "command": command,
            "verdict": verdict.action.value,
            "reason": verdict.reason,
            "rule_id": verdict.rule_id,
            "source": "layer_a",
        }
        await self._bus.publish({"type": "opsec.verdict", "payload": payload})

    @staticmethod
    def _strip_command(command: str, raw: str) -> str:
        """Strip leading command name from raw to give Layer A the arg string."""
        prefix = f"{command} "
        return raw[len(prefix) :] if raw.startswith(prefix) else raw
