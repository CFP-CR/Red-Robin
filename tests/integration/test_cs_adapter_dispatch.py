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
    AdapterConfig,
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
    from redrobin.application import cs_adapter_lifecycle as cal

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
