"""Assistant — reactive grounded-recommendation bus consumer (Task 2).

Mirrors WorkingMemoryEnricher: subscribes to the bus, PUBLISHES (never writes
memory), runs degraded-None (no llm -> silent), and never crashes the daemon
(every per-event exception is logged and swallowed). It caches the latest
working_memory_snapshot (the L2 push context) and, on each enrichment.completed,
runs the signal-not-noise gate -> one agent cycle -> publishes
advisor.recommendation. Emits observability via the recommendation event + logs.
"""

from __future__ import annotations

import logging
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

    async def run(self) -> None:
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("assistant on_started callback raised")
        try:
            while True:
                evt = await sub.get()
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
                    continue
                if etype != "enrichment.completed":
                    continue
                try:
                    await self._on_enrichment(evt.get("payload") or {})
                except Exception:
                    logger.exception("assistant failed to process enrichment.completed")
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


__all__ = ["Assistant"]
