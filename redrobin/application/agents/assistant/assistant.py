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
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

from redrobin.application.agents.assistant.agent import (
    apply_deltas_to_snapshot,
    run_answer_cycle,
    run_cycle,
    run_interpretation_cycle,
    state_signature,
)
from redrobin.application.agents.assistant.interpret import (
    is_own_proposal_question,
    milestone_events,
    resolve_confirm,
)
from redrobin.application.agents.assistant.lessons import LessonStore, capture_lesson
from redrobin.application.agents.assistant.llm import AgentLLM
from redrobin.application.agents.assistant.triggers import TriggerGate
from redrobin.application.event_bus import EventBus
from redrobin.domain.assistant import AgentTool
from redrobin.domain.memory import PendingMilestone

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
        lesson_store: LessonStore | None = None,
        engagement_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._tools = tools
        self._trigger = trigger
        self._summary_reader = summary_reader
        self._enabled = enabled
        self._max_tokens = max_tokens
        self._wm_max_items = wm_max_items
        self._lesson_store = lesson_store
        self._engagement_provider = engagement_provider
        self._wm_snapshot: dict[str, Any] = {}
        self.on_started: Callable[[], None] | None = None
        # Proactive cadence (spec §6.A): a debounced background check, no scheduler.
        self._clock = clock
        self._lull_sec = lull_sec
        self._proactive_min_interval = proactive_min_interval_sec
        self._last_beat: float | None = None  # last beacon beat OR lull-check; drives the debounce
        self._last_proactive: float | None = None  # proactive rate-limit (N)
        self._material_pending = False  # a new defence awaits the post-reactive backstop
        self._last_interp_sig: str | None = None  # interpretation novelty gate (signal-not-noise)

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
                        try:
                            # Forward-merge THIS command's freshly-extracted facts so the
                            # cycle fired on the following enrichment.completed reasons over
                            # fresh working memory, not the one-step-stale snapshot.
                            # Material trigger = a NEW defence (D3), not a re-observed one: the
                            # non-dedup forward-merge would otherwise change the state_signature
                            # and the novelty gate would not suppress a wasted proactive cycle.
                            prior_defenses = set(self._wm_snapshot.get("defenses_observed") or [])
                            self._wm_snapshot = apply_deltas_to_snapshot(self._wm_snapshot, payload)
                            new_defenses = payload.get("defenses_observed") or []
                            if any(d not in prior_defenses for d in new_defenses):
                                # a new defence — backstop after reactive
                                self._material_pending = True
                        except Exception:
                            logger.exception("assistant failed to merge working_memory.deltas")
                    continue
                if etype == "operator.question":
                    self._last_beat = self._clock()  # a question is activity — defer the lull
                    try:
                        await self._on_question(evt.get("payload") or {})
                    except Exception:
                        logger.exception("assistant failed to process operator.question")
                    continue
                if etype == "operator.answer":
                    # resolving a confirm is activity — defer the lull
                    self._last_beat = self._clock()
                    try:
                        await self._on_confirm(evt.get("payload") or {})
                    except Exception:
                        logger.exception("assistant failed to process operator.answer")
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
                # the interpretation write (never-crash, novelty-gated)
                await self._safe_interpret()
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
        async with self._thinking():
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

    async def _on_question(self, payload: dict[str, Any]) -> None:
        if is_own_proposal_question(payload):
            return  # our own confirm proposal, not an operator ask (no loop)
        if self._llm is None or not self._enabled:
            return
        question = str(payload.get("text") or "").strip()
        question_id = str(payload.get("question_id") or payload.get("id") or "")
        if not question:
            return
        try:
            l5_summary = self._summary_reader()
        except Exception:
            logger.exception("assistant: summary_reader failed; proceeding with empty L5")
            l5_summary = ""
        async with self._thinking():
            parsed = await run_answer_cycle(
                llm=self._llm,
                tools=self._tools,
                wm_snapshot=self._wm_snapshot,
                l5_summary=l5_summary,
                question=question,
                max_tokens=self._max_tokens,
                wm_max_items=self._wm_max_items,
            )
        if parsed is None:
            return  # degraded / unparseable -> no reply
        engagement_id = (
            self._engagement_provider() if self._engagement_provider is not None else None
        )
        if parsed.lesson is not None:
            le = capture_lesson(
                parsed.lesson,
                engagement_id=engagement_id,
                now=datetime.now(),
                store=self._lesson_store,
            )
            if le is not None:
                logger.info("assistant: captured %s-scoped lesson: %s", le.scope, le.lesson)
        await self._bus.publish(
            {
                "type": "operator.answer",
                "payload": {
                    "question_id": question_id,
                    "text": parsed.answer,
                    "citations": parsed.citations,
                },
            }
        )
        logger.info("assistant: published operator.answer question_id=%s", question_id)

    async def _safe_interpret(self) -> None:
        if self._llm is None or not self._enabled or not self._wm_snapshot:
            return
        try:
            # Novelty gate: only interpret when the state changed since the last interpretation
            # (bounds cost + stops re-firing the same confirm every command — signal-not-noise).
            sig = state_signature(self._wm_snapshot)
            if sig == self._last_interp_sig:
                return
            self._last_interp_sig = sig
            try:
                l5 = self._summary_reader()
            except Exception:
                logger.exception("assistant: summary_reader failed; empty L5")
                l5 = ""
            async with self._thinking():
                it = await run_interpretation_cycle(
                    llm=self._llm,
                    tools=self._tools,
                    wm_snapshot=self._wm_snapshot,
                    l5_summary=l5,
                    max_tokens=self._max_tokens,
                    wm_max_items=self._wm_max_items,
                )
            if it is None:
                return
            # Don't re-publish a confirm for an id already pending (no duplicate operator.question).
            if it.needs_confirm and any(
                str(p.get("milestone_id")) == it.milestone_id
                for p in (self._wm_snapshot.get("pending_milestones") or [])
                if isinstance(p, dict)
            ):
                return
            for evt in milestone_events(it):
                await self._bus.publish(evt)
            logger.info(
                "assistant: interpretation %s milestone=%s confirm=%s",
                "proposed" if it.needs_confirm else "auto-write",
                it.milestone_id,
                it.needs_confirm,
            )
        except Exception:
            logger.exception("assistant: interpretation write failed")

    async def _on_confirm(self, payload: dict[str, Any]) -> None:
        if self._llm is None or not self._enabled:
            return
        pend_raw = self._wm_snapshot.get("pending_milestones") or []
        pend: list[PendingMilestone] = []
        for p in pend_raw:
            if isinstance(p, dict):
                try:
                    pend.append(PendingMilestone(**p))
                except TypeError:
                    continue  # a malformed pending dict is skipped, never crashes
        evt = resolve_confirm(answer_payload=payload, pending_milestones=pend)
        if evt is None:
            return  # not a confirm for one of our pending proposals — ignore (no loop)
        await self._bus.publish(evt)
        logger.info(
            "assistant: confirm resolved milestone=%s drop=%s",
            evt["payload"].get("milestone_id"),
            evt["payload"].get("drop", False),
        )

    async def _emit_thinking(self, state: str) -> None:
        """Publish the transient working signal (state 'start' / 'stop'). NEVER-crash: a
        thinking signal must never break a cycle, so a publish failure is logged + swallowed."""
        try:
            await self._bus.publish({"type": "assistant.thinking", "payload": {"state": state}})
        except Exception:
            logger.exception("assistant: thinking signal publish failed")

    @contextlib.asynccontextmanager
    async def _thinking(self) -> AsyncIterator[None]:
        """Bracket an LLM-backed cycle with assistant.thinking start/stop. The stop is in a
        finally, so a raising cycle still clears the indicator (the spinner never sticks)."""
        await self._emit_thinking("start")
        try:
            yield
        finally:
            await self._emit_thinking("stop")

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
        async with self._thinking():
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
