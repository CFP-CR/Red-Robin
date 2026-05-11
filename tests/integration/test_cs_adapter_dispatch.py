"""Slice 9 integration: CS adapter loaded from config, dispatch path works.

Closes Phase 2 gap #8 — ``CSRestClient`` was implemented but never instantiated
by the daemon. This module verifies the adapter loader reads YAML configs from
disk, builds a logged-in REST client per config, and stashes them on
``app.state.cs_adapters`` for ALLOW-verdict dispatch.

The CS REST surface we target is documented in ``adapters/cobaltstrike/adapter.py``:
``POST /api/auth/login`` returns ``{"access_token": ...}``; dispatch goes to
``POST /api/v1/beacons/{bid}/consoleCommand``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from redrobin.application.cs_adapter_lifecycle import (
    build_adapter,
    load_adapter_configs,
)


def _write_adapter_config(root: Path, name: str, **kwargs) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.yaml"
    lines = [f"name: {name}", "kind: cobaltstrike"]
    for k, v in kwargs.items():
        lines.append(f"{k}: {v}")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_loader_finds_yaml(tmp_path):
    _write_adapter_config(
        tmp_path,
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
    )
    configs = load_adapter_configs(tmp_path)
    assert len(configs) == 1
    assert configs[0].name == "cs-main"
    assert configs[0].kind == "cobaltstrike"


def test_loader_ignores_non_yaml_and_other_kinds(tmp_path):
    _write_adapter_config(
        tmp_path,
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
    )
    # Wrong kind — should be skipped silently
    (tmp_path / "sliver.yaml").write_text(
        "name: sliver-1\nkind: sliver\nbase_url: https://s\n"
    )
    # Non-yaml — ignored
    (tmp_path / "notes.txt").write_text("not a config")
    configs = load_adapter_configs(tmp_path)
    assert [c.name for c in configs] == ["cs-main"]


def test_loader_returns_empty_when_dir_missing(tmp_path):
    assert load_adapter_configs(tmp_path / "does-not-exist") == []


@pytest.mark.asyncio
async def test_build_adapter_returns_logged_in_client(tmp_path):
    _write_adapter_config(
        tmp_path,
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
        verify_ssl="false",
    )
    cfg = load_adapter_configs(tmp_path)[0]

    def handler(req: httpx.Request) -> httpx.Response:
        if "/api/auth/login" in str(req.url):
            return httpx.Response(200, json={"access_token": "tkn-abc"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = await build_adapter(cfg, transport=transport)
    try:
        assert client._token == "tkn-abc"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_lifespan_loads_adapters(tmp_path, monkeypatch):
    _write_adapter_config(
        tmp_path / "adapters",
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
        verify_ssl="false",
    )
    monkeypatch.setenv("REDROBIN_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json")
    )

    from redrobin.api.app import create_app
    # Patch the infrastructure module (not the facade) — the facade re-exports
    # the function by reference at import time, but ``build_adapter`` resolves
    # ``_default_transport`` against its own module namespace.
    from redrobin.infrastructure import cs_adapter_loader as cal

    def handler(req: httpx.Request) -> httpx.Response:
        if "/api/auth/login" in str(req.url):
            return httpx.Response(200, json={"access_token": "tkn-test"})
        if "/consoleCommand" in str(req.url):
            return httpx.Response(200, json={"task_id": "t-1"})
        return httpx.Response(404)

    monkeypatch.setattr(
        cal, "_default_transport", lambda: httpx.MockTransport(handler)
    )
    app = create_app()
    async with app.router.lifespan_context(app):
        assert "cs-main" in app.state.cs_adapters
        client = app.state.cs_adapters["cs-main"]
        assert client._token == "tkn-test"
        result = await client.dispatch_console_command("B-1", "ipconfig")
        assert result == {"task_id": "t-1"}


@pytest.mark.asyncio
async def test_lifespan_starts_clean_without_adapter_dir(tmp_path, monkeypatch):
    """No adapter YAML present → daemon boots with empty registry, no crash."""
    monkeypatch.setenv("REDROBIN_ADAPTERS_DIR", str(tmp_path / "nope"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json")
    )

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.cs_adapters == {}


@pytest.mark.asyncio
async def test_allow_verdict_dispatches_through_adapter(tmp_path, monkeypatch):
    """ALLOW (PASS) verdict + adapter+bid in request body → command POSTed to CS."""
    import asyncio

    from fastapi.testclient import TestClient

    _write_adapter_config(
        tmp_path / "adapters",
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
        verify_ssl="false",
    )
    monkeypatch.setenv("REDROBIN_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json")
    )

    # Patch the infrastructure module (not the facade) — the facade re-exports
    # the function by reference at import time, but ``build_adapter`` resolves
    # ``_default_transport`` against its own module namespace.
    from redrobin.infrastructure import cs_adapter_loader as cal

    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/api/auth/login" in url:
            return httpx.Response(200, json={"access_token": "tkn"})
        if "/consoleCommand" in url:
            # /api/v1/beacons/{bid}/consoleCommand → extract bid
            bid = url.split("/beacons/")[1].split("/")[0]
            try:
                body = req.read().decode()
                cmd = body
            except Exception:  # pragma: no cover - defensive
                cmd = ""
            seen.append((bid, cmd))
            return httpx.Response(200, json={"task_id": "t-1"})
        return httpx.Response(404)

    monkeypatch.setattr(
        cal, "_default_transport", lambda: httpx.MockTransport(handler)
    )

    from redrobin.api.app import create_app
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app = create_app()
    with TestClient(app) as client:
        token = get_or_create_token()
        r = client.post(
            "/v1/verdict/quick",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "bid": "B-42",
                "command": "pwd",
                "raw": "pwd",
                "source_event_id": "",
                "adapter": "cs-main",
            },
        )
        assert r.status_code == 200
        assert r.json()["action"] == "PASS"

        # Dispatch is fire-and-forget; give the event loop a tick to drain it.
        # Poll briefly so the test stays fast when dispatch lands synchronously.
        for _ in range(20):
            if seen:
                break
            await asyncio.sleep(0.05)
        assert len(seen) == 1
        bid, body = seen[0]
        assert bid == "B-42"
        assert "pwd" in body


@pytest.mark.asyncio
async def test_allow_verdict_without_adapter_field_does_not_dispatch(
    tmp_path, monkeypatch
):
    """No 'adapter' in the request → verdict succeeds, nothing POSTed to CS."""
    from fastapi.testclient import TestClient

    _write_adapter_config(
        tmp_path / "adapters",
        "cs-main",
        teamserver_url="https://ts.local:50050",
        operator_user="op",
        operator_password="x",
        verify_ssl="false",
    )
    monkeypatch.setenv("REDROBIN_ADAPTERS_DIR", str(tmp_path / "adapters"))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv(
        "REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json")
    )

    # Patch the infrastructure module (not the facade) — the facade re-exports
    # the function by reference at import time, but ``build_adapter`` resolves
    # ``_default_transport`` against its own module namespace.
    from redrobin.infrastructure import cs_adapter_loader as cal

    dispatched: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/api/auth/login" in url:
            return httpx.Response(200, json={"access_token": "tkn"})
        if "/consoleCommand" in url:
            dispatched.append(url)
            return httpx.Response(200, json={"task_id": "t-1"})
        return httpx.Response(404)

    monkeypatch.setattr(
        cal, "_default_transport", lambda: httpx.MockTransport(handler)
    )

    from redrobin.api.app import create_app
    from redrobin.infrastructure.transport.bearer import get_or_create_token

    app = create_app()
    with TestClient(app) as client:
        token = get_or_create_token()
        r = client.post(
            "/v1/verdict/quick",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "bid": "B-42",
                "command": "pwd",
                "raw": "pwd",
                "source_event_id": "",
            },
        )
        assert r.status_code == 200
        assert dispatched == []
