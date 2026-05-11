"""WorkingMemoryReducer — subscribes to EventBus and writes to WorkingMemoryRepo.

Slice 4 Task 4.1: builds the reducer in isolation. Task 4.2 will start it as a
lifespan task and wire the snapshot read path; for now the reducer is exercised
only by its own integration test.

Event dispatch table:

* ``c2.beacon_output``  → regex-parse output for NTLM hashes, hostnames, and
  Kerberos artifacts; write credentials/hosts/findings.
* ``c2.beacon_checkin`` → upsert a ``Session`` keyed by ``beacon_id``.
* ``opsec.verdict``     → when ``verdict == "BLOCK"``, append a finding.

All other event types are silently ignored. Repo helpers are synchronous JSON
load/save; they run on the loop thread because per-event payload size is tiny.

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
from redrobin.domain.memory import Credential, Finding, Host, Session
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
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._engagement_id = engagement_id
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
                    self._dispatch(evt)
                except Exception:
                    logger.exception(
                        "reducer failed to process event type=%s", evt.get("type")
                    )
        finally:
            await self._bus.unsubscribe(sub)

    def _dispatch(self, evt: dict[str, Any]) -> None:
        etype = evt.get("type")
        payload_raw = evt.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        if etype == "c2.beacon_output":
            self._on_beacon_output(payload)
        elif etype == "c2.beacon_checkin":
            self._on_beacon_checkin(payload)
        elif etype == "opsec.verdict":
            self._on_verdict(payload)
        # else: silently ignore — the bus is fan-out, not all events are for us.

    def _on_beacon_output(self, payload: dict[str, Any]) -> None:
        output = str(payload.get("output", ""))
        beacon_id_raw = payload.get("beacon_id")
        beacon_id = str(beacon_id_raw) if beacon_id_raw is not None else None

        for m in _NTLM_LINE.finditer(output):
            cred = Credential(
                domain=m.group("domain"),
                username=m.group("user"),
                ntlm_hash=m.group("hash"),
                source_beacon_id=beacon_id,
            )
            self._repo.upsert_credential(cred)

        m_host = _HOSTNAME_LINE.search(output)
        if m_host is not None:
            host = Host(name=m_host.group("hostname").upper())
            self._repo.upsert_host(host)

        if _KERBEROS_TICKET.search(output) is not None:
            finding = Finding(
                kind="kerberos_ticket_observed",
                summary=f"Kerberos artifacts seen on beacon {beacon_id}",
                source_beacon_id=beacon_id,
            )
            self._repo.append_finding(finding)

    def _on_beacon_checkin(self, payload: dict[str, Any]) -> None:
        beacon_id_raw = payload.get("beacon_id")
        if beacon_id_raw is None:
            return
        beacon_id = str(beacon_id_raw)
        hostname_raw = payload.get("hostname")
        user_raw = payload.get("user")
        integrity_raw = payload.get("integrity")
        session = Session(
            beacon_id=beacon_id,
            hostname=str(hostname_raw) if hostname_raw is not None else None,
            user=str(user_raw) if user_raw is not None else None,
            integrity=str(integrity_raw) if integrity_raw is not None else None,
        )
        self._repo.upsert_session(session)

    def _on_verdict(self, payload: dict[str, Any]) -> None:
        if payload.get("verdict") != "BLOCK":
            return
        command = payload.get("command", "?")
        reason = payload.get("reason", "")
        beacon_id_raw = payload.get("beacon_id")
        beacon_id = str(beacon_id_raw) if beacon_id_raw is not None else None
        finding = Finding(
            kind="opsec_block",
            summary=f"Blocked command: {command} — {reason}",
            source_beacon_id=beacon_id,
        )
        self._repo.append_finding(finding)


__all__ = ["WorkingMemoryReducer"]
