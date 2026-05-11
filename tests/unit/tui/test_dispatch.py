"""OperatorDispatcher unit tests using httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from redrobin.tui.dispatch import OperatorDispatcher


@pytest.mark.asyncio
async def test_submit_posts_to_verdict_quick_route():
    captured: dict[str, httpx.Request] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return httpx.Response(
            200,
            json={
                "action": "PASS",
                "reason": "passthrough",
                "rule_id": None,
                "suggestions": [],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token="tkn"
        )
        result = await dispatcher.submit("whoami")

    assert result["action"] == "PASS"
    req = captured["req"]
    assert req.method == "POST"
    assert str(req.url) == "http://127.0.0.1:8765/v1/verdict/quick"


@pytest.mark.asyncio
async def test_submit_carries_bearer_header_when_token_present():
    captured: dict[str, httpx.Request] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token="my-secret-token"
        )
        await dispatcher.submit("whoami")

    assert captured["req"].headers.get("authorization") == "Bearer my-secret-token"
    assert captured["req"].headers.get("content-type", "").startswith("application/json")


@pytest.mark.asyncio
async def test_submit_omits_authorization_when_token_is_none():
    captured: dict[str, httpx.Request] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token=None
        )
        await dispatcher.submit("whoami")

    assert "authorization" not in {k.lower() for k in captured["req"].headers}


@pytest.mark.asyncio
async def test_submit_body_includes_correlation_command_raw_and_bid():
    captured: dict[str, dict] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token="t"
        )
        await dispatcher.submit("ls /etc", beacon_id="B-1")

    body = captured["body"]
    # The daemon binds correlation_id := source_event_id; we wire it through
    # source_event_id so the verdict pipeline emits it back on the bus.
    assert body["source_event_id"]
    assert body["source_event_id"] != ""
    assert body["command"] == "ls /etc"
    assert body["raw"] == "ls /etc"
    assert body["bid"] == "B-1"


@pytest.mark.asyncio
async def test_submit_uses_empty_bid_when_beacon_missing():
    captured: dict[str, dict] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token=None
        )
        await dispatcher.submit("ls")

    # Daemon's request model expects bid: str (required), so we default to ""
    # when the operator has no beacon context selected.
    assert captured["body"]["bid"] == ""


@pytest.mark.asyncio
async def test_submit_raises_on_http_error():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token=None
        )
        with pytest.raises(httpx.HTTPStatusError):
            await dispatcher.submit("whoami")


@pytest.mark.asyncio
async def test_submit_strips_trailing_slash_from_base_url():
    captured: dict[str, httpx.Request] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765///", token=None
        )
        await dispatcher.submit("whoami")

    # No doubled slashes in the assembled URL.
    assert str(captured["req"].url) == "http://127.0.0.1:8765/v1/verdict/quick"


@pytest.mark.asyncio
async def test_submit_generates_unique_correlation_per_call():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body["source_event_id"])
        return httpx.Response(200, json={"action": "PASS"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = OperatorDispatcher(
            client=client, base_url="http://127.0.0.1:8765", token=None
        )
        await dispatcher.submit("a")
        await dispatcher.submit("b")
        await dispatcher.submit("c")

    assert len(seen) == 3
    assert len(set(seen)) == 3, "expected unique correlation IDs across calls"
