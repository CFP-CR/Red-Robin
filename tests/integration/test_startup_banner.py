"""Slice 7: startup banner."""

from __future__ import annotations

from redrobin.application.startup_banner import format_banner
from redrobin.infrastructure.config import Settings


def test_banner_includes_resolved_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setenv("VOYAGE_API_KEY", "k2")
    s = Settings()
    banner = format_banner(
        settings=s,
        engagement_name="op-test",
        version="0.1.1",
        adapter_names=["cs-main"],
    )
    assert "op-test" in banner
    assert "cs-main" in banner
    assert str(tmp_path) in banner
    # Secrets MUST NEVER appear
    assert "k1" not in banner
    assert "k2" not in banner


def test_banner_shows_anthropic_key_status_not_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "supersecret")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings()
    banner = format_banner(
        settings=s,
        engagement_name=None,
        version="0.1.1",
        adapter_names=[],
    )
    assert "supersecret" not in banner
    assert "set" in banner.lower() or "MISSING" in banner


def test_banner_shows_missing_keys_as_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings()
    banner = format_banner(
        settings=s,
        engagement_name=None,
        version="0.1.1",
        adapter_names=[],
    )
    assert "MISSING" in banner  # for at least one key


def test_banner_warns_when_scope_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings()
    banner = format_banner(
        settings=s,
        engagement_name="acme",
        version="0.1.1",
        adapter_names=[],
        scope_empty=True,
    )
    assert "EMPTY" in banner
    assert "no target boundary" in banner


def test_banner_no_scope_warning_when_scoped(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings()
    banner = format_banner(
        settings=s,
        engagement_name="acme",
        version="0.1.1",
        adapter_names=[],
        scope_empty=False,
    )
    assert "EMPTY" not in banner
