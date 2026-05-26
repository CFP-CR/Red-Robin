"""Slice 3: fail-fast boot validation."""

from __future__ import annotations

import pytest


def test_missing_anthropic_key_aborts_when_strict(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "0")

    from redrobin.infrastructure.boot_validation import validate_for_boot
    from redrobin.infrastructure.config import Settings

    with pytest.raises(SystemExit) as exc:
        validate_for_boot(Settings())
    assert exc.value.code == 2


def test_missing_anthropic_key_allowed_when_degraded_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")

    from redrobin.infrastructure.boot_validation import validate_for_boot
    from redrobin.infrastructure.config import Settings

    validate_for_boot(Settings())  # must not raise
    captured = capsys.readouterr()
    assert "degraded" in captured.err.lower() or "degraded" in captured.out.lower()


def test_paths_writable_check_fails_on_readonly(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    bad_root = tmp_path / "nonexistent" / "deeply" / "nested"
    monkeypatch.setenv("REDROBIN_ROOT", str(bad_root))
    # The validator should NOT silently mkdir — it should require root to exist
    from redrobin.infrastructure.boot_validation import validate_for_boot
    from redrobin.infrastructure.config import Settings

    with pytest.raises(SystemExit) as exc:
        validate_for_boot(Settings(), require_root_exists=True)
    assert exc.value.code == 2


def test_validator_passes_when_keys_present(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    from redrobin.infrastructure.boot_validation import validate_for_boot
    from redrobin.infrastructure.config import Settings

    validate_for_boot(Settings())  # must not raise


def test_engagement_slug_missing_yaml_warns_but_passes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_SLUG", "op-no-yaml-yet")
    # No engagement.yaml on disk
    from redrobin.infrastructure.boot_validation import validate_for_boot
    from redrobin.infrastructure.config import Settings

    validate_for_boot(Settings())  # must not raise (warns only)
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert "op-no-yaml-yet" in combined or "wildcard" in combined
