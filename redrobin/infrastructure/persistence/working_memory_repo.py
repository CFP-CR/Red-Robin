"""redrobin/infrastructure/persistence/working_memory_repo.py — JSON-backed working memory.

Per spec §5.2: working memory lives at `~/.redrobin/engagements/<slug>/working_memory.json`.
Mutable (no immutable history); operator-curated + auto-updated from events.

Slice 4 adds idempotent helpers (``upsert_*``, ``append_finding``,
``list_credentials``) used by ``WorkingMemoryReducer``. Each helper is a
synchronous load → mutate → save against the single-engagement JSON file; the
reducer runs on the event loop but the I/O footprint is small enough that
running it directly is acceptable for v0.1. If contention becomes an issue we
will swap in an async write-behind layer here.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from redrobin.domain.memory import (
    Account,
    Credential,
    Finding,
    Host,
    Objective,
    Session,
    ToolUsage,
    WorkingMemory,
)


class WorkingMemoryRepo:
    """Read/write a single engagement's working memory JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> WorkingMemory:
        if not self.path.exists():
            raise FileNotFoundError(f"working memory not found: {self.path}")
        data = json.loads(self.path.read_text())
        return self._from_dict(data)

    def save(self, wm: WorkingMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: serialize to a sibling temp file, then os.replace() it
        # onto the target. A reader (a cross-thread load() in the daemon, or a
        # polling test) therefore sees either the old or the new complete file —
        # never the empty window a plain truncate-then-write exposes, which
        # caused a JSONDecodeError under concurrent reads. os.replace is atomic
        # on POSIX; on Windows it raises PermissionError while a reader holds the
        # target open, so retry briefly before giving up.
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps(asdict(wm), indent=2, default=str))
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 9:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    # --- Slice 4 reducer helpers ----------------------------------------- #

    def upsert_credential(self, cred: Credential) -> None:
        """Insert or replace a credential keyed by (domain, username)."""
        wm = self.load()
        key = (cred.domain.lower(), cred.username.lower())
        wm.credentials = [
            c for c in wm.credentials if (c.domain.lower(), c.username.lower()) != key
        ]
        wm.credentials.append(cred)
        self.save(wm)

    def list_credentials(self) -> list[Credential]:
        return list(self.load().credentials)

    def upsert_host(self, host: Host) -> None:
        """Insert or merge a host keyed by uppercase name.

        Non-empty fields on the incoming host overwrite existing values; existing
        values are preserved when the incoming field is ``None``/empty so that
        successive observations enrich the record without erasing prior data.
        """
        wm = self.load()
        normalized_name = host.name.upper()
        host.name = normalized_name
        for existing in wm.hosts:
            if existing.name.upper() == normalized_name:
                existing.os = host.os or existing.os
                existing.integrity_level = host.integrity_level or existing.integrity_level
                existing.edr_detected = host.edr_detected or existing.edr_detected
                existing.access_level = host.access_level or existing.access_level
                self.save(wm)
                return
        wm.hosts.append(host)
        self.save(wm)

    def upsert_session(self, session: Session) -> None:
        """Insert or replace a session keyed by beacon_id."""
        wm = self.load()
        wm.sessions = [s for s in wm.sessions if s.beacon_id != session.beacon_id]
        wm.sessions.append(session)
        self.save(wm)

    def append_finding(self, finding: Finding) -> None:
        """Append a finding (no dedup — findings are an audit stream)."""
        wm = self.load()
        wm.findings.append(finding)
        self.save(wm)

    # --- (de)serialization ---------------------------------------------- #

    @staticmethod
    def _from_dict(data: dict) -> WorkingMemory:
        return WorkingMemory(
            engagement_id=data["engagement_id"],
            client=data.get("client", ""),
            scope_in_scope=data.get("scope_in_scope", []),
            scope_out_of_scope=data.get("scope_out_of_scope", []),
            objectives=[Objective(**o) for o in data.get("objectives", [])],
            current_phase=data.get("current_phase", "not_started"),
            kill_chain_status=data.get("kill_chain_status", {}),
            rules_of_engagement=data.get("rules_of_engagement", []),
            hosts=[Host(**h) for h in data.get("hosts", [])],
            accounts=[Account(**a) for a in data.get("accounts", [])],
            defenses_observed=data.get("defenses_observed", []),
            tools_used=[ToolUsage(**t) for t in data.get("tools_used", [])],
            credentials=[Credential(**c) for c in data.get("credentials", [])],
            findings=[Finding(**f) for f in data.get("findings", [])],
            sessions=[Session(**s) for s in data.get("sessions", [])],
        )
