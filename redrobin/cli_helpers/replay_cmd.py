"""``redrobin replay`` business logic — extracted in Slice 12.

The CLI wrapper in :mod:`redrobin.cli` loads ``.env`` and resolves the bearer
token; :func:`run_replay` here owns the asyncio + httpx plumbing so the body
is testable without argparse plumbing.

Reuses :func:`redrobin.cli_helpers.replay.replay_to_daemon` (the bulk-replay
helper that does the actual JSONL streaming).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from redrobin.application.transport_auth import get_or_create_token
from redrobin.cli_helpers.replay import replay_to_daemon


def resolve_token(explicit: str | None) -> str | None:
    """Return ``explicit`` if provided, else read the operator's local token."""
    if explicit is not None:
        return explicit
    try:
        return get_or_create_token()
    except Exception:
        return None


def run_replay(
    *,
    archive: Path,
    base_url: str,
    token: str | None,
) -> int:
    if not archive.exists():
        print(f"[!] {archive} does not exist", file=sys.stderr)
        return 1
    if not archive.is_file():
        print(f"[!] {archive} is not a regular file", file=sys.stderr)
        return 1

    async def _run() -> int:
        async with httpx.AsyncClient() as client:
            return await replay_to_daemon(
                file=archive, client=client, base_url=base_url, token=token
            )

    try:
        n = asyncio.run(_run())
    except httpx.HTTPError as e:
        print(f"[!] replay failed: {e}", file=sys.stderr)
        return 1
    print(f"[+] Replayed {n} event(s) to {base_url}")
    return 0
