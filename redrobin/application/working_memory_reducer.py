"""WorkingMemoryReducer — subscribes to EventBus and writes to WorkingMemoryRepo.

Slice 4 Task 4.1: builds the reducer in isolation. Task 4.2 will start it as a
lifespan task and wire the snapshot read path; for now the reducer is exercised
only by its own integration test.

Event dispatch table (canonical wire types from ``redrobin/domain/events.py``):

* ``beacon.command_output``  → regex-parse ``output`` for NTLM hashes,
  hostnames, and Kerberos artifacts; write credentials/hosts/findings.
* ``beacon.initial``         → upsert a ``Session`` keyed by ``bid``.
* ``opsec.verdict``          → when ``verdict == "BLOCK"``, append a finding.

All other event types are silently ignored. Repo helpers are synchronous JSON
load/save; they run on the loop thread because per-event payload size is tiny.

Field-name conventions follow the wire schema:

* Beacon identifier is ``bid`` (not ``beacon_id``).
* Host on ``beacon.initial`` is ``host`` (not ``hostname``).
* User integrity level on ``beacon.initial`` is ``integrity_level`` (not ``integrity``).
* ``output`` and ``user`` map 1:1.

The reducer is single-engagement: it carries an ``engagement_id`` for logging
and tagging but does not multiplex repos. A future Task 4.x will fan out one
reducer per active engagement if needed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from redrobin.application.event_bus import EventBus
from redrobin.domain.memory import (
    Credential,
    Finding,
    Host,
    Session,
    working_memory_to_snapshot,
)
from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

logger = logging.getLogger(__name__)

# Username line: "Username: DOMAIN\\user" or "User: DOMAIN\\user".
# The NTLM hash line follows on a later line: "NTLM: <lm32>:<nt32>" (case-insensitive).
_NTLM_LINE: re.Pattern[str] = re.compile(
    r"(?:Username|User)\s*[:=]\s*(?P<domain>[\w.-]+)\\(?P<user>[\w.-]+)\s*[\r\n]+"
    r"\s*(?:NTLM|Hash)\s*[:=]\s*(?P<hash>[a-f0-9]{32}:[a-f0-9]{32})",
    re.IGNORECASE,
)

# Kerberos artefact heuristic — klist output, "Cached Tickets", or any krbtgt reference.
_KERBEROS_TICKET: re.Pattern[str] = re.compile(
    r"klist|Ticket(?:s)?\s+cached|Cached\s+Tickets|krbtgt", re.IGNORECASE
)

# Hostname declared by Cobalt Strike commands (``hostname``, ``ipconfig``, ``systeminfo``).
_HOSTNAME_LINE: re.Pattern[str] = re.compile(
    r"(?:Hostname|Computer\s*Name|ComputerName|Host\s*Name)\s*[:=]\s*(?P<hostname>[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)


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
        # ``opsec.verdict`` event is appended as an Episode so the
        # ``LessonPromoter`` has a full audit trail to aggregate over.
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
        if etype == "beacon.command_output":
            self._on_beacon_command_output(payload)
            return True
        elif etype == "beacon.initial":
            self._on_beacon_initial(payload)
            return True
        elif etype == "opsec.verdict":
            self._on_verdict(payload)
            return True
        # else: silently ignore — the bus is fan-out, not all events are for us.
        return False

    def _on_beacon_command_output(self, payload: dict[str, Any]) -> None:
        output = str(payload.get("output", ""))
        # Canonical wire field is ``bid`` (per protocol/schemas/v0.1).
        bid_raw = payload.get("bid")
        bid = str(bid_raw) if bid_raw is not None else None

        for m in _NTLM_LINE.finditer(output):
            cred = Credential(
                domain=m.group("domain"),
                username=m.group("user"),
                ntlm_hash=m.group("hash"),
                source_beacon_id=bid,
            )
            self._repo.upsert_credential(cred)

        m_host = _HOSTNAME_LINE.search(output)
        if m_host is not None:
            host = Host(name=m_host.group("hostname").upper())
            self._repo.upsert_host(host)

        if _KERBEROS_TICKET.search(output) is not None:
            finding = Finding(
                kind="kerberos_ticket_observed",
                summary=f"Kerberos artifacts seen on beacon {bid}",
                source_beacon_id=bid,
            )
            self._repo.append_finding(finding)

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

    def _on_verdict(self, payload: dict[str, Any]) -> None:
        verdict = payload.get("verdict", "UNKNOWN")
        command = payload.get("command", "?")
        reason = payload.get("reason", "")
        beacon_id_raw = payload.get("beacon_id")
        beacon_id = str(beacon_id_raw) if beacon_id_raw is not None else None

        # Slice 10: every verdict (not only BLOCK) becomes an Episode so the
        # promoter sees full operator activity. Failures here must not crash
        # the reducer — the working-memory finding path below is more
        # important for live operators.
        if self._episodic is not None:
            try:
                self._episodic.record(
                    engagement_id=self._engagement_id,
                    correlation_id=str(payload.get("correlation_id", "") or ""),
                    command=str(command),
                    verdict=str(verdict),
                    reason=str(reason),
                )
            except Exception:
                logger.exception("episodic record failed for verdict=%s", verdict)

        # Finding append still only fires on BLOCK — the working-memory
        # findings stream is a curated operator-visible audit list, not a
        # full event log.
        if verdict != "BLOCK":
            return
        finding = Finding(
            kind="opsec_block",
            summary=f"Blocked command: {command} — {reason}",
            source_beacon_id=beacon_id,
        )
        self._repo.append_finding(finding)


__all__ = ["WorkingMemoryReducer"]
