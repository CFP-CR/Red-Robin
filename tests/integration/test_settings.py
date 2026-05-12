"""Slice 1: typed Settings via pydantic-settings."""
from __future__ import annotations

import pytest

from redrobin.infrastructure.config import Settings


def test_settings_defaults_when_env_empty(monkeypatch, tmp_path):
    for k in [
        "ANTHROPIC_API_KEY", "VOYAGE_API_KEY",
        "REDROBIN_ROOT", "REDROBIN_RULES_DIR", "REDROBIN_WORKING_MEMORY_PATH",
        "REDROBIN_EPISODIC_PATH", "REDROBIN_LONGTERM_PATH", "REDROBIN_SUMMARY_PATH",
        "REDROBIN_ENGAGEMENTS_DIR", "REDROBIN_ENGAGEMENT_SLUG", "REDROBIN_ADAPTERS_DIR",
        "REDROBIN_PROMOTER_INTERVAL_SEC", "REDROBIN_PROMOTER_MIN_OCCURRENCES",
        "REDROBIN_L5_WINDOW", "REDROBIN_L5_INTERVAL_SEC",
        "REDROBIN_PORT", "REDROBIN_HOST", "REDROBIN_PID_FILE",
    ]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 7777
    assert s.promoter_interval_sec == 60.0
    assert s.promoter_min_occurrences == 3
    assert s.l5_window == 25
    assert s.l5_interval_sec == 30.0
    assert s.rules_dir == tmp_path / "rules"
    assert s.working_memory_path == tmp_path / "working_memory.json"
    assert s.episodic_path == tmp_path / "episodic.db"


def test_settings_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_PORT", "8888")
    monkeypatch.setenv("REDROBIN_PROMOTER_INTERVAL_SEC", "120")
    s = Settings()
    assert s.port == 8888
    assert s.promoter_interval_sec == 120.0


def test_settings_typo_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_PORT", "eight thousand")
    with pytest.raises(Exception) as exc:
        Settings()
    assert "REDROBIN_PORT" in str(exc.value) or "port" in str(exc.value).lower()


def test_settings_paths_derive_from_root(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / "rr"))
    s = Settings()
    assert s.rules_dir == tmp_path / "rr" / "rules"
    assert s.engagements_dir == tmp_path / "rr" / "engagements"
    assert s.adapters_dir == tmp_path / "rr" / "adapters"
    assert s.pid_file == tmp_path / "rr" / "daemon.pid"
    assert s.transport_token_file == tmp_path / "rr" / "transport.token"


def test_settings_explicit_path_overrides_root_derivation(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / "rr"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "custom_wm.json"))
    s = Settings()
    assert s.working_memory_path == tmp_path / "custom_wm.json"
    # Other paths still root-derived
    assert s.episodic_path == tmp_path / "rr" / "episodic.db"
