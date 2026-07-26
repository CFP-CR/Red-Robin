"""``redrobin doctor`` business logic — extracted from cli.py in Slice 12.

The CLI wrapper :func:`redrobin.cli.cmd_doctor` is a thin shell that loads
``.env`` and constructs ``Settings()``; :func:`run_doctor` here does the real
work so the body is testable without going through argparse.
"""

from __future__ import annotations

import asyncio

from redrobin.cli_helpers.doctor_probes import (
    probe_anthropic,
    probe_cs_adapter,
    probe_voyage,
)
from redrobin.infrastructure.config import Settings
from redrobin.infrastructure.cs_adapter_loader import load_adapter_configs
from redrobin.infrastructure.filesystem import redrobin_root


def run_doctor(*, settings: Settings, quick: bool) -> int:
    """Run preflight checks; return 0 on OK, 1 on degraded/failures.

    Slice 6 (v0.1.x hardening): real connectivity probes against the Anthropic
    API, Voyage API, and any configured CS adapter so the operator catches
    network/credential failures during preflight instead of at first dispatch.
    ``quick=True`` skips network probes for fast CI smoke tests.
    """
    failures: list[str] = []
    print("[*] Red Robin doctor (preflight check)")
    print("Configuration:")
    print(f"  ROOT:               {settings.root}")
    print(f"  ENGAGEMENT:         {settings.engagement_slug or '(none)'}")

    # ANTHROPIC_API_KEY
    if settings.anthropic_api_key:
        print("  ANTHROPIC_API_KEY:  set")
    else:
        print("  ANTHROPIC_API_KEY:  MISSING - assistant/enricher/summariser disabled")
        failures.append("ANTHROPIC_API_KEY not set")

    # VOYAGE_API_KEY
    if settings.voyage_api_key:
        print("  VOYAGE_API_KEY:     set")
    else:
        print("  VOYAGE_API_KEY:     MISSING - KB retrieval will be empty")
        failures.append("VOYAGE_API_KEY not set")

    # ~/.redrobin/ layout
    root = redrobin_root()
    if root.exists():
        print(f"  ROOT_EXISTS:        yes ({root})")
    else:
        print("  ROOT_EXISTS:        NO - run `redrobin init`")
        failures.append(f"{root} not initialized")

    # --quick: env-var presence check only, skip network.
    if quick:
        if failures:
            print("[+] Doctor finished: DEGRADED (quick mode - skipped network probes)")
            return 1
        print("[+] Doctor finished: OK (quick mode - skipped network probes)")
        return 0

    async def run_probes() -> list:
        results = []
        if settings.anthropic_api_key:
            results.append(await probe_anthropic(api_key=settings.anthropic_api_key))
        if settings.voyage_api_key:
            results.append(await probe_voyage(api_key=settings.voyage_api_key))
        if settings.adapters_dir is not None and settings.adapters_dir.is_dir():
            for cfg in load_adapter_configs(settings.adapters_dir):
                results.append(
                    await probe_cs_adapter(
                        teamserver_url=cfg.teamserver_url,
                        operator_user=cfg.operator_user,
                        operator_password=cfg.operator_password,
                        verify_ssl=cfg.verify_ssl,
                    )
                )
        return results

    results = asyncio.run(run_probes())
    if results:
        print("\nConnectivity:")
        for r in results:
            status = "OK " if r.ok else "FAIL"
            reason = f"  {r.reason}" if r.reason else ""
            print(f"  [{status}] {r.name:30s} {r.latency_ms:6.0f}ms{reason}")
            if not r.ok:
                failures.append(f"{r.name}: {r.reason}")

    if failures:
        print("[+] Doctor finished: DEGRADED (command capture still works)")
        return 1
    print("[+] Doctor finished: OK")
    return 0
