"""adapters/sliver-mock/adapter.py — Synthetic Sliver event replayer.

Replays a recorded scenario JSON file against the Red Robin daemon. Each entry
has `delay_ms` for realistic timing. Used for demos (DEF CON talk) and protocol
validation (proves the wire protocol works for non-CS C2).

Usage:
    redrobin-sliver-mock --scenario linux_demo
    redrobin-sliver-mock --scenario acme_corp --speed 2x
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ADAPTER_PACKAGE = Path(__file__).resolve().parent
SCENARIOS_DIR = ADAPTER_PACKAGE / "scenarios"


def _load_manifest() -> dict:
    import tomllib
    return tomllib.loads((ADAPTER_PACKAGE / "manifest.toml").read_text())


def _resolve_token() -> str:
    """Bearer token from REDROBIN_TOKEN env or ~/.redrobin/transport.token."""
    token = os.environ.get("REDROBIN_TOKEN")
    if token:
        return token
    token_path = Path.home() / ".redrobin" / "transport.token"
    if not token_path.exists():
        raise RuntimeError(
            "REDROBIN_TOKEN env var not set and ~/.redrobin/transport.token missing. "
            "Start the daemon first: `redrobin daemon start`."
        )
    return token_path.read_text().strip()


def _load_scenario(name: str) -> list[dict]:
    path = SCENARIOS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"scenario {name!r} not found. Available: "
            f"{[p.stem for p in SCENARIOS_DIR.glob('*.json')]}"
        )
    return json.loads(path.read_text())


async def _replay(scenario: list[dict], daemon_url: str, token: str, speed: float) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0)) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Connect
        manifest = _load_manifest()
        r = await client.post(
            f"{daemon_url}/v1/adapters/connect",
            json={"manifest": manifest},
            headers=headers,
        )
        r.raise_for_status()
        session_id = r.json()["adapter_session_id"]
        print(f"[sliver-mock] connected as session {session_id}", file=sys.stderr)

        # 2. Replay events with delays
        for entry in scenario:
            delay_ms = entry.get("delay_ms", 0)
            envelope = entry["envelope"]
            # Inject the active session_id
            envelope["source"]["adapter_session_id"] = session_id

            if delay_ms > 0 and speed > 0:
                await asyncio.sleep(delay_ms / 1000.0 / speed)

            r = await client.post(
                f"{daemon_url}/v1/events", json=envelope, headers=headers
            )
            r.raise_for_status()
            event_type = envelope["event"]["type"]
            print(f"[sliver-mock] sent {event_type}", file=sys.stderr)

        # 3. Disconnect
        await client.post(
            f"{daemon_url}/v1/adapters/disconnect",
            json={"adapter_session_id": session_id},
            headers=headers,
        )
        print("[sliver-mock] disconnected", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="redrobin-sliver-mock")
    parser.add_argument("--scenario", required=True, help="scenario name (without .json)")
    parser.add_argument(
        "--daemon", default="http://127.0.0.1:7777", help="daemon URL"
    )
    parser.add_argument(
        "--speed", type=float, default=1.0, help="replay speed multiplier (1.0 = real-time, 2.0 = 2x)"
    )
    args = parser.parse_args()

    scenario = _load_scenario(args.scenario)
    token = _resolve_token()
    asyncio.run(_replay(scenario, args.daemon, token, args.speed))


if __name__ == "__main__":
    main()
