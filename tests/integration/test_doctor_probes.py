"""Slice 6: doctor probes real connectivity."""

from __future__ import annotations

import httpx
import pytest

from redrobin.cli_helpers.doctor_probes import (
    probe_anthropic,
    probe_voyage,
    probe_cs_adapter,
)


@pytest.mark.asyncio
async def test_probe_anthropic_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"text": "ok"}]})

    transport = httpx.MockTransport(handler)
    result = await probe_anthropic(api_key="k", transport=transport)
    assert result.ok
    assert result.latency_ms > 0


@pytest.mark.asyncio
async def test_probe_anthropic_unauth():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api-key"}})

    transport = httpx.MockTransport(handler)
    result = await probe_anthropic(api_key="bad", transport=transport)
    assert not result.ok
    assert "401" in result.reason or "invalid" in result.reason.lower()


@pytest.mark.asyncio
async def test_probe_voyage_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.0] * 10}]})

    transport = httpx.MockTransport(handler)
    result = await probe_voyage(api_key="k", transport=transport)
    assert result.ok


@pytest.mark.asyncio
async def test_probe_cs_adapter_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        if "/api/auth/login" in str(req.url):
            return httpx.Response(200, json={"access_token": "tkn"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await probe_cs_adapter(
        teamserver_url="https://ts.example.com:50050",
        operator_user="u",
        operator_password="p",
        verify_ssl=False,
        transport=transport,
    )
    assert result.ok


@pytest.mark.asyncio
async def test_probe_anthropic_missing_key():
    """Missing API key returns ok=False with descriptive reason, no network call."""
    result = await probe_anthropic(api_key="", transport=None)
    assert not result.ok
    assert "not set" in result.reason.lower() or "missing" in result.reason.lower()


@pytest.mark.asyncio
async def test_probe_cs_adapter_network_failure():
    """If transport raises (e.g., connection refused), probe returns ok=False with reason."""

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    result = await probe_cs_adapter(
        teamserver_url="https://ts.unreachable.local",
        operator_user="u",
        operator_password="p",
        verify_ssl=False,
        transport=transport,
    )
    assert not result.ok
    assert result.reason  # non-empty
