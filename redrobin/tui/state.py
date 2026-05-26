"""Mutable TUI state shared by panels and event listener.

Slice 8 — the WebSocket client mutates this container as events stream in; the
prompt_toolkit panels render directly from it on every paint. Layer A and
Layer B verdicts share a correlation_id, so retroactive corrections update
the original verdict line in place instead of appending duplicate events.
"""

from __future__ import annotations

from collections import deque
from typing import Any


class TUIState:
    """In-memory store for streaming events, the latest working-memory snapshot,
    and overlay state. Single-threaded mutation from the asyncio loop only.
    """

    def __init__(self, max_events: int = 500) -> None:
        self._max = max_events
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.snapshot: dict[str, Any] = {
            "engagement_id": None,
            "hosts": [],
            "credentials": [],
            "findings": [],
            "sessions": [],
        }
        self.focus_overlay: str | None = None
        self.selected_correlation_id: str | None = None

    def append_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def replace_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot

    def update_verdict(self, correlation_id: str, fields: dict[str, Any]) -> None:
        """Mutate the most recent ``opsec.verdict`` matching ``correlation_id``.

        If no match is found we append a fresh event so the user still sees the
        retroactive correction, even if the original Layer A event was evicted
        from the bounded deque.
        """
        for e in reversed(self.events):
            if e.get("type") != "opsec.verdict":
                continue
            payload = e.get("payload") or {}
            if payload.get("correlation_id") == correlation_id:
                payload.update(fields)
                e["payload"] = payload
                return
        self.events.append(
            {
                "type": "opsec.verdict",
                "payload": {"correlation_id": correlation_id, **fields},
            }
        )

    def latest_verdict(self) -> dict[str, Any] | None:
        for e in reversed(self.events):
            if e.get("type") == "opsec.verdict":
                return e
        return None
