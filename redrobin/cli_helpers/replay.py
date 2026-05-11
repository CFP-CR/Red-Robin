"""Replay helper: stream a JSONL trace file into the daemon's event bus.

Reads ``file`` one line at a time, parses each as JSON, and POSTs it to
``{base_url}/v1/events/replay``. The daemon-side endpoint re-publishes the
body onto the in-process event bus so all subscribers — reducer, TUI WS,
verdict pipeline — see replayed events as if they were live.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx


async def replay_to_daemon(
    *,
    file: Path,
    client: httpx.AsyncClient,
    base_url: str,
    token: str | None,
) -> int:
    """POST each non-blank JSONL line to the daemon. Returns the number sent."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base_url.rstrip("/") + "/v1/events/replay"

    count = 0
    with file.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            event = json.loads(line)
            r = await client.post(url, json=event, headers=headers)
            r.raise_for_status()
            count += 1
    return count
