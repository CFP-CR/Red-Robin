"""Posts operator input to the daemon's ``/v1/verdict/quick`` endpoint.

The daemon's ``QuickVerdictRequest`` model requires ``bid``, ``command``, and
``raw`` and accepts an optional ``source_event_id``. The verdict pipeline binds
``correlation_id := source_event_id`` when it publishes ``opsec.verdict`` on
the bus, so this is the same identifier the TUI uses to correlate Layer A and
Layer B verdicts.
"""

from __future__ import annotations

import uuid

import httpx


class OperatorDispatcher:
    """Tiny async wrapper around ``POST /v1/verdict/quick``.

    The HTTP client is injected so callers (and tests) own its lifecycle. The
    same instance can be reused across many submissions — uvicorn-side this is
    a single keep-alive connection.
    """

    def __init__(self, *, client: httpx.AsyncClient, base_url: str, token: str | None) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._token = token

    async def submit(self, command: str, *, beacon_id: str | None = None) -> dict:
        """POST one operator command to the verdict endpoint and return the JSON body."""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        correlation_id = uuid.uuid4().hex
        body = {
            # The daemon binds correlation_id := source_event_id on the bus.
            "source_event_id": correlation_id,
            "command": command,
            "raw": command,
            # bid is required by QuickVerdictRequest; default to "" when the
            # operator hasn't selected a beacon context.
            "bid": beacon_id if beacon_id is not None else "",
        }
        resp = await self._client.post(
            f"{self._base_url}/v1/verdict/quick", json=body, headers=headers
        )
        resp.raise_for_status()
        return resp.json()
