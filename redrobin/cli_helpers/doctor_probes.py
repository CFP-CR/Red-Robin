"""Real connectivity probes for `redrobin doctor`."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class ProbeResult:
    name: str
    ok: bool
    latency_ms: float
    reason: str = ""


async def probe_anthropic(
    *, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None
) -> ProbeResult:
    if not api_key:
        return ProbeResult(
            name="anthropic",
            ok=False,
            latency_ms=0.0,
            reason="ANTHROPIC_API_KEY not set",
        )
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as c:
            resp = await c.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return ProbeResult(name="anthropic", ok=True, latency_ms=elapsed)
            return ProbeResult(
                name="anthropic",
                ok=False,
                latency_ms=elapsed,
                reason=f"HTTP {resp.status_code}",
            )
    except Exception as e:
        return ProbeResult(
            name="anthropic",
            ok=False,
            latency_ms=(time.monotonic() - start) * 1000,
            reason=str(e),
        )


async def probe_voyage(
    *, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None
) -> ProbeResult:
    if not api_key:
        return ProbeResult(
            name="voyage",
            ok=False,
            latency_ms=0.0,
            reason="VOYAGE_API_KEY not set",
        )
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as c:
            resp = await c.post(
                "https://api.voyageai.com/v1/embeddings",
                json={"input": ["ping"], "model": "voyage-4-large"},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
            )
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return ProbeResult(name="voyage", ok=True, latency_ms=elapsed)
            return ProbeResult(
                name="voyage",
                ok=False,
                latency_ms=elapsed,
                reason=f"HTTP {resp.status_code}",
            )
    except Exception as e:
        return ProbeResult(
            name="voyage",
            ok=False,
            latency_ms=(time.monotonic() - start) * 1000,
            reason=str(e),
        )
