"""Slice 1: typed Settings via pydantic-settings."""

from __future__ import annotations

import pytest

from redrobin.infrastructure.config import Settings


def test_settings_defaults_when_env_empty(monkeypatch, tmp_path):
    for k in [
        "ANTHROPIC_API_KEY",
        "VOYAGE_API_KEY",
        "REDROBIN_ROOT",
        "REDROBIN_RULES_DIR",
        "REDROBIN_WORKING_MEMORY_PATH",
        "REDROBIN_EPISODIC_PATH",
        "REDROBIN_LONGTERM_PATH",
        "REDROBIN_SUMMARY_PATH",
        "REDROBIN_ENGAGEMENTS_DIR",
        "REDROBIN_ENGAGEMENT_SLUG",
        "REDROBIN_PROMOTER_INTERVAL_SEC",
        "REDROBIN_PROMOTER_MIN_OCCURRENCES",
        "REDROBIN_L5_WINDOW",
        "REDROBIN_L5_INTERVAL_SEC",
        "REDROBIN_PORT",
        "REDROBIN_HOST",
        "REDROBIN_PID_FILE",
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
    assert s.pid_file == tmp_path / "rr" / "daemon.pid"
    assert s.transport_token_file == tmp_path / "rr" / "transport.token"


def test_settings_explicit_path_overrides_root_derivation(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / "rr"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "custom_wm.json"))
    s = Settings()
    assert s.working_memory_path == tmp_path / "custom_wm.json"
    # Other paths still root-derived
    assert s.episodic_path == tmp_path / "rr" / "episodic.db"


@pytest.mark.asyncio
async def test_app_state_has_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    from redrobin.api.app import create_app
    from redrobin.infrastructure.config import Settings

    app = create_app()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.settings, Settings)
        assert app.state.settings.root == tmp_path
        # Existing app.state attributes still work
        assert app.state.event_bus is not None
        assert app.state.working_memory_repo is not None


def test_filesystem_root_reads_from_settings(monkeypatch, tmp_path):
    """Audit pass 2 #C2: filesystem.py must not hardcode Path.home() / '.redrobin'."""
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / "custom"))
    # Reload to ensure no cached module-level constant
    import importlib
    from redrobin.infrastructure import filesystem

    importlib.reload(filesystem)
    # Whatever accessor we expose (function or property), it must reflect env
    resolved = filesystem.redrobin_root()
    assert resolved == tmp_path / "custom"


def test_bearer_token_path_reads_from_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path / "custom"))
    import importlib
    from redrobin.infrastructure.transport import bearer

    importlib.reload(bearer)
    path = bearer._token_path()
    assert path == tmp_path / "custom" / "transport.token"


def test_enrichment_settings_defaults_and_override(monkeypatch):
    from redrobin.infrastructure.config import Settings

    s = Settings()
    assert s.enrichment_enabled is True
    assert s.enrichment_model == "claude-haiku-4-5"
    assert s.enrichment_min_output_chars == 8
    assert s.enrichment_chunk_max_chars == 16000
    assert s.enrichment_denylist == []  # enrich-by-default; denylist is opt-in via env

    monkeypatch.setenv("REDROBIN_ENRICHMENT_ENABLED", "false")
    monkeypatch.setenv("REDROBIN_ENRICHMENT_CHUNK_MAX_CHARS", "32000")
    s2 = Settings()
    assert s2.enrichment_enabled is False
    assert s2.enrichment_chunk_max_chars == 32000

    # denylist env override accepts a comma-separated string OR a JSON array
    monkeypatch.setenv("REDROBIN_ENRICHMENT_DENYLIST", "foo, bar ,baz")
    assert Settings().enrichment_denylist == ["foo", "bar", "baz"]
    monkeypatch.setenv("REDROBIN_ENRICHMENT_DENYLIST", '["x", "y"]')
    assert Settings().enrichment_denylist == ["x", "y"]


def test_enrichment_hardening_settings_defaults_and_override(monkeypatch):
    from redrobin.infrastructure.config import Settings

    s = Settings()
    assert s.enrichment_max_concurrency == 4
    assert s.enrichment_self_annotation_prefix == "[Red Robin]"

    monkeypatch.setenv("REDROBIN_ENRICHMENT_MAX_CONCURRENCY", "8")
    monkeypatch.setenv("REDROBIN_ENRICHMENT_SELF_ANNOTATION_PREFIX", "[RR]")
    s2 = Settings()
    assert s2.enrichment_max_concurrency == 8
    assert s2.enrichment_self_annotation_prefix == "[RR]"


def test_enrichment_max_concurrency_rejects_below_one(monkeypatch):
    import pytest

    from redrobin.infrastructure.config import Settings

    monkeypatch.setenv("REDROBIN_ENRICHMENT_MAX_CONCURRENCY", "0")
    with pytest.raises(Exception) as exc:
        Settings()
    assert ">= 1" in str(exc.value) or "enrichment_max_concurrency" in str(exc.value).lower()
