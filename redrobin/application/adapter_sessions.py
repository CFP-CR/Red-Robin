"""redrobin/application/adapter_sessions.py — adapter session registry.

In-memory session registry. Adapters POST to `/v1/adapters/connect` and receive
a session_id; subsequent events include `source.adapter_session_id` for
disambiguation across reconnects (per spec §4 Componente 8 lifecycle).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ulid import ULID


@dataclass
class AdapterSession:
    session_id: str
    manifest: dict[str, Any]
    connected_at: float
    last_heartbeat: float = field(default_factory=time.time)


class AdapterSessionRegistry:
    """Process-local in-memory registry of connected adapter sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, AdapterSession] = {}

    def connect(self, manifest: dict[str, Any]) -> AdapterSession:
        session_id = f"ses_{ULID()}"
        now = time.time()
        session = AdapterSession(
            session_id=session_id,
            manifest=manifest,
            connected_at=now,
            last_heartbeat=now,
        )
        self._sessions[session_id] = session
        return session

    def heartbeat(self, session_id: str) -> AdapterSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.last_heartbeat = time.time()
        return session

    def disconnect(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def get(self, session_id: str) -> AdapterSession | None:
        return self._sessions.get(session_id)

    def all_active(self, *, stale_after_seconds: float = 30.0) -> list[AdapterSession]:
        now = time.time()
        return [
            s for s in self._sessions.values() if (now - s.last_heartbeat) < stale_after_seconds
        ]


# Singleton process-local registry. FastAPI dependency in adapters_router
# returns this instance; tests can swap by monkeypatching this module.
_REGISTRY = AdapterSessionRegistry()


def get_registry() -> AdapterSessionRegistry:
    return _REGISTRY
