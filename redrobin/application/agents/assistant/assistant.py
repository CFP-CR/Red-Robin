"""Assistant — reactive grounded-recommendation bus consumer (Task 2).

Mirrors WorkingMemoryEnricher: subscribes to the bus, PUBLISHES (never writes
memory), runs degraded-None (no llm -> silent), and never crashes the daemon
(every per-event exception is logged and swallowed). It caches the latest
working_memory_snapshot (the L2 push context) and, on each enrichment.completed,
runs the signal-not-noise gate -> one agent cycle -> publishes
advisor.recommendation. Emits observability via the recommendation event + logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from redrobin.application.agents.assistant.agent import (
    apply_deltas_to_snapshot,
    run_cycle,
    state_signature,
)
from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.domain.assistant import AgentTool

logger = logging.getLogger(__name__)


class Assistant:
    def __init__(
        self,
        *,
        bus: EventBus,
        llm: AgentLLM | None,
        tools: list[AgentTool],
        trigger: TriggerGate,
        summary_reader: Callable[[], str],
        enabled: bool = True,
        max_tokens: int = 2048,
        wm_max_items: int = 20,
        clock: Callable[[], float] = time.monotonic,
        lull_sec: float = 300.0,
        proactive_min_interval_sec: float = 600.0,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._tools = tools
        self._trigger = trigger
        self._summary_reader = summary_reader
        self._enabled = enabled
        self._max_tokens = max_tokens
        self._wm_max_items = wm_max_items
        self._wm_snapshot: dict[str, Any] = {}
        self.on_started: Callable[[], None] | None = None
        # Proactive cadence (spec §6.A): a debounced background check, no scheduler.
        self._clock = clock
        self._lull_sec = lull_sec
        self._proactive_min_interval = proactive_min_interval_sec
        self._last_beat: float | None = None  # last beacon beat OR lull-check; drives the debounce
        self._last_proactive: float | None = None  # proactive rate-limit (N)
        self._material_pending = False  # a new defence awaits the post-reactive backstop

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("assistant on_started callback raised")
        try:
            while True:
                timeout = self._lull_timeout()
                if timeout == 0.0:
                    # Lull already due at loop-top — fire without a zero wait_for.
                    self._last_beat = self._clock()
                    await self._safe_proactive("lull")
                    continue
                try:
                    evt = (
                        await asyncio.wait_for(sub.get(), timeout)
                        if timeout is not None
                        else await sub.get()
                    )
                except TimeoutError:
                    self._last_beat = self._clock()  # reset the debounce (no busy-loop)
                    await self._safe_proactive("lull")
                    continue
                if evt.get("_type") == "_close":
                    return
                etype = evt.get("type")
                if etype == "working_memory_snapshot":
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        self._wm_snapshot = payload  # authoritative replace
                    continue
                if etype == "working_memory.deltas":
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        # Forward-merge THIS command's freshly-extracted facts so the
                        # cycle fired on the following enrichment.completed reasons over
                        # fresh working memory, not the one-step-stale snapshot.
                        self._wm_snapshot = apply_deltas_to_snapshot(self._wm_snapshot, payload)
                        if payload.get("defenses_observed"):
                            self._material_pending = True  # a new defence — backstop after reactive
                    continue
                if etype != "enrichment.completed":
                    continue
                self._last_beat = self._clock()  # a beacon beat resets the lull debounce
                try:
                    await self._on_enrichment(evt.get("payload") or {})
                except Exception:
                    logger.exception("assistant failed to process enrichment.completed")
                if self._material_pending:
                    self._material_pending = False
                    await self._safe_proactive("material_change")
        finally:
            await self._bus.unsubscribe(sub)

    async def _on_enrichment(self, payload: dict[str, Any]) -> None:
        if self._llm is None or not self._enabled:
            return
        beacon_id = payload.get("beacon_id")
        trigger_command = str(payload.get("command") or "")
        # Reactive attach/dedup handle (enrichment.completed has no wire correlation_id;
        # beacon_id may be None, so fall back to 'unknown').
        trigger_key: str | None = (
            f"{beacon_id or 'unknown'}:{trigger_command}" if trigger_command else None
        )
        sig = state_signature(self._wm_snapshot)
        if not self._trigger.should_speak(correlation_id=trigger_key, state_signature=sig):
            return
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        rec = await run_cycle(
            llm=self._llm,
            tools=self._tools,
            wm_snapshot=self._wm_snapshot,
            l5_summary=l5_summary,
            trigger_command=trigger_command,
            correlation_id=trigger_key,
            max_tokens=self._max_tokens,
            wm_max_items=self._wm_max_items,
        )
        if rec is None:
            return  # signal-not-noise: the model stayed silent, or the output was unusable
        self._trigger.record_spoken(correlation_id=trigger_key, state_signature=sig)
        await self._bus.publish({"type": "advisor.recommendation", "payload": rec.to_payload()})
        logger.info(
            "assistant: published advisor.recommendation command=%s beacon=%s risk=%s",
            trigger_command,
            beacon_id,
            rec.opsec_risk.value,
        )

    def _lull_timeout(self) -> float | None:
        """Seconds until the next proactive lull check, or None when proactive is
        inactive (disabled / degraded / no beacon beat yet). DYNAMIC — computed
        from the last beat, NOT a fixed periodic timer (spec §6.A: no scheduler)."""
        if self._llm is None or not self._enabled or self._last_beat is None:
            return None
        return max(0.0, self._lull_sec - (self._clock() - self._last_beat))

    async def _safe_proactive(self, reason: str) -> None:
        """Never-crash wrapper (spec §6.E): a proactive cycle's failure is logged,
        the run loop survives."""
        try:
            await self._maybe_proactive(reason)
        except Exception:
            logger.exception("assistant: proactive check failed reason=%s", reason)

    async def _maybe_proactive(self, reason: str) -> None:
        """One proactive (self-triggered) check. Reuses the SAME signal-not-noise
        gate as reactive (novelty makes it a backstop that never crowds reactive;
        cooldown shared) plus a separate proactive rate-limit (N). Publishes the
        SAME advisor.recommendation with correlation_id=None."""
        if self._llm is None or not self._enabled:
            return
        if not self._wm_snapshot:
            return  # nothing to reason about yet — don't burn a cycle on empty state
        now = self._clock()
        if (
            self._last_proactive is not None
            and (now - self._last_proactive) < self._proactive_min_interval
        ):
            return  # proactive rate-limit: <= 1 proactive utterance / N
        sig = state_signature(self._wm_snapshot)
        if not self._trigger.should_speak(correlation_id=None, state_signature=sig):
            return  # SAME gate — novelty/cooldown backstop
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        rec = await run_cycle(
            llm=self._llm,
            tools=self._tools,
            wm_snapshot=self._wm_snapshot,
            l5_summary=l5_summary,
            trigger_command="",
            correlation_id=None,
            max_tokens=self._max_tokens,
            wm_max_items=self._wm_max_items,
            proactive=True,
        )
        if rec is None:
            return  # signal-not-noise: nothing material
        self._trigger.record_spoken(correlation_id=None, state_signature=sig)
        self._last_proactive = now
        await self._bus.publish({"type": "advisor.recommendation", "payload": rec.to_payload()})
        logger.info(
            "assistant: published proactive advisor.recommendation reason=%s risk=%s",
            reason,
            rec.opsec_risk.value,
        )


__all__ = ["Assistant"]
