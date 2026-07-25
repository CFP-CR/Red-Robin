"""WorkingMemoryReducer — subscribes to EventBus and writes to WorkingMemoryRepo.

Slice 4 Task 4.1: builds the reducer in isolation. Task 4.2 will start it as a
lifespan task and wire the snapshot read path; for now the reducer is exercised
only by its own integration test.

Event dispatch table (canonical wire types from ``redrobin/domain/events.py``):

* ``beacon.initial``         → upsert a ``Session`` keyed by ``bid``.
* ``beacon.command_input``   → append an L3 ``Episode`` (raw activity log). Mutates no
  working-memory field, so the snapshot is deliberately NOT re-emitted.
* ``working_memory.deltas``  → merge an enrichment deltas batch via
  ``WorkingMemoryRepo.apply_deltas`` (single writer; provenance system-supplied).

``beacon.command_output`` is consumed by the LLM enricher
(``redrobin/application/agents/enricher/enricher.py``), a separate bus consumer that
publishes ``working_memory.deltas`` — the reducer no longer parses it.

All other event types are silently ignored. Repo helpers are synchronous JSON
load/save; they run on the loop thread because per-event payload size is tiny.

Field-name conventions follow the wire schema:

* Beacon identifier is ``bid`` (not ``beacon_id``).
* Host on ``beacon.initial`` is ``host`` (not ``hostname``).
* User integrity level on ``beacon.initial`` is ``integrity_level`` (not ``integrity``).
* ``user`` maps 1:1 (only ``_on_beacon_initial`` reads it now; ``output`` is the LLM enricher's input, not the reducer's).

The reducer is single-engagement: it carries an ``engagement_id`` for logging
and tagging but does not multiplex repos. A future Task 4.x will fan out one
reducer per active engagement if needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from redrobin.application.event_bus import EventBus
from redrobin.domain.memory import (
    Session,
    WorkingMemoryDeltas,
    working_memory_to_snapshot,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

logger = logging.getLogger(__name__)


class WorkingMemoryReducer:
    """Consumes EventBus events and writes derived state to WorkingMemoryRepo."""

    def __init__(
        self,
        bus: EventBus,
        repo: WorkingMemoryRepo,
        engagement_id: str,
        episodic_repo: EpisodicRepo | None = None,
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._engagement_id = engagement_id
        # Slice 10: optional Episodic store. When provided, every
        # ``beacon.command_input`` event is appended as an Episode so the L5
        # summariser has a full audit trail to compress.
        self._episodic = episodic_repo
        # Optional callback invoked once the reducer has subscribed and is ready
        # to consume events. Tests use this to avoid sleep-based races; production
        # callers can leave it unset.
        self.on_started: Callable[[], None] | None = None

    async def run(self) -> None:
        """Subscribe to the bus and dispatch each event until close sentinel."""
        sub = await self._bus.subscribe()
        if self.on_started is not None:
            try:
                self.on_started()
            except Exception:
                logger.exception("on_started callback raised")
        try:
            while True:
                evt = await sub.get()
                if evt.get("_type") == "_close":
                    return
                try:
                    mutated = self._dispatch(evt)
                except Exception:
                    logger.exception("reducer failed to process event type=%s", evt.get("type"))
                    mutated = False
                if mutated:
                    await self._publish_snapshot()
        finally:
            await self._bus.unsubscribe(sub)

    async def _publish_snapshot(self) -> None:
        """Publish a COMPLETE working_memory_snapshot to the bus so connected
        TUI clients update live (#146). One re-emit per handled event (not per
        repo.save), full snapshot (not a delta) — payloads are tiny. Safe from
        loops: the reducer also receives this event, but ``working_memory_snapshot``
        is not in ``_dispatch``'s table so it is ignored (returns False)."""
        try:
            wm = self._repo.load()
        except FileNotFoundError:
            return
        await self._bus.publish(
            {"type": "working_memory_snapshot", "payload": working_memory_to_snapshot(wm)}
        )

    def _dispatch(self, evt: dict[str, Any]) -> bool:
        """Dispatch one event to its handler. Returns True if the event was a
        handled (working-memory-mutating) type, so ``run()`` knows to re-emit
        the snapshot."""
        etype = evt.get("type")
        payload_raw = evt.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        if etype == "beacon.initial":
            self._on_beacon_initial(payload)
            return True
        elif etype == "beacon.command_input":
            # L3 activity log. Returns False: no working-memory field changed, so the
            # reducer must NOT broadcast a full snapshot for every command.
            self._on_command_input(payload)
            return False
        elif etype == "working_memory.deltas":
            self._on_enrichment_deltas(payload)
            return True
        elif etype == "engagement.milestone":
            self._on_milestone(payload)
            return True
        # else: silently ignore — the bus is fan-out, not all events are for us.
        return False

    def _on_beacon_initial(self, payload: dict[str, Any]) -> None:
        # Canonical wire fields: ``bid``, ``host``, ``user``, ``integrity_level``.
        bid_raw = payload.get("bid")
        if bid_raw is None:
            return
        bid = str(bid_raw)
        host_raw = payload.get("host")
        user_raw = payload.get("user")
        integrity_raw = payload.get("integrity_level")
        session = Session(
            beacon_id=bid,
            hostname=str(host_raw) if host_raw is not None else None,
            user=str(user_raw) if user_raw is not None else None,
            integrity=str(integrity_raw) if integrity_raw is not None else None,
        )
        self._repo.upsert_session(session)

    def _on_command_input(self, payload: dict[str, Any]) -> None:
        """Record one Episode per beacon command — the raw activity log L5 narrates from.

        Replaces the verdict stream as L3's source: the gate happened to fire on every
        command, so ``opsec.verdict`` was a de-facto command stream; ``beacon.command_input``
        is the real one and needs no LLM. Never raises — a failed episode write must not
        stop the reducer.
        """
        if self._episodic is None:
            return
        command = str(payload.get("command") or "").strip()
        if not command:
            return
        try:
            self._episodic.record(
                engagement_id=self._engagement_id,
                correlation_id=str(payload.get("correlation_id") or ""),
                command=command,
            )
        except Exception:
            logger.exception("episodic record failed for command=%s", command)

    def _on_enrichment_deltas(self, payload: dict[str, Any]) -> None:
        """Merge an enrichment deltas batch. Provenance is taken from the
        event's top-level ``source_beacon_id`` (system-supplied), not from any
        per-item value, so the producer cannot spoof it. The reducer is the
        single writer to working memory (see ``apply_deltas``)."""
        src = payload.get("source_beacon_id")
        deltas = WorkingMemoryDeltas.from_payload(
            payload, source_beacon_id=str(src) if src is not None else None
        )
        self._repo.apply_deltas(deltas)

    def _on_milestone(self, payload: dict[str, Any]) -> None:
        """Interpretation write (spec §6.C) — the assistant proposes, the reducer
        (single writer) applies. Never crashes the reducer."""
        try:
            self._repo.apply_milestone(
                phase=str(payload.get("phase") or ""),
                milestone_id=str(payload.get("milestone_id") or ""),
                status=str(payload.get("status") or ""),
                progress_pct=int(payload.get("progress_pct") or 0),
                pending=bool(payload.get("pending")),
                drop=bool(payload.get("drop")),
                prompt=str(payload.get("prompt") or ""),
            )
        except Exception:
            logger.exception("reducer: engagement.milestone apply failed")


__all__ = ["WorkingMemoryReducer"]
