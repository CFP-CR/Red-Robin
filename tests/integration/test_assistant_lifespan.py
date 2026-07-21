"""The assistant boots (degraded, no API key) as a lifespan task, and settings
carry the assistant knobs. Mirrors test_enricher.py::test_lifespan_starts_enricher_task."""

from __future__ import annotations

import pytest


def test_settings_expose_assistant_knobs(monkeypatch):
    monkeypatch.delenv("REDROBIN_ASSISTANT_MODEL", raising=False)
    from redrobin.application.config import Settings

    s = Settings()
    # Off by default (dark-launched): opt in with REDROBIN_ASSISTANT_ENABLED=1
    # until the whole assistant ships. The lifespan task still boots either way;
    # `enabled` only gates whether a cycle publishes (see test_assistant.py).
    assert s.assistant_enabled is False
    assert s.assistant_model == "claude-haiku-4-5"
    assert s.assistant_cooldown_sec == 30.0
    assert s.assistant_max_tokens == 2048
    assert s.assistant_wm_max_items == 20


def test_settings_assistant_model_override(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_MODEL", "claude-sonnet-4-6")
    from redrobin.application.config import Settings

    assert Settings().assistant_model == "claude-sonnet-4-6"


def test_settings_assistant_wm_max_items_override(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_WM_MAX_ITEMS", "5")
    from redrobin.application.config import Settings

    assert Settings().assistant_wm_max_items == 5


def test_settings_assistant_wm_max_items_rejects_below_one(monkeypatch):
    import pytest as _pytest

    monkeypatch.setenv("REDROBIN_ASSISTANT_WM_MAX_ITEMS", "0")
    from redrobin.application.config import Settings

    with _pytest.raises(Exception) as exc:
        Settings()
    assert ">= 1" in str(exc.value) or "assistant_wm_max_items" in str(exc.value).lower()


def test_start_assistant_is_importable():
    from redrobin.api.lifespan_helpers import start_assistant

    assert callable(start_assistant)


@pytest.mark.asyncio
async def test_lifespan_starts_assistant_task(tmp_path, monkeypatch):
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-asst")
    # No ANTHROPIC_API_KEY / VOYAGE_API_KEY -> assistant boots degraded (llm None), task still runs.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.assistant_task is not None
        assert not app.state.assistant_task.done()

    # Clean teardown: the assistant drains via the bus _close sentinel (close_bus
    # runs before close_assistant), NOT the 2.0s cancel fallback.
    assert app.state.assistant_task.done()
    assert app.state.assistant_task.cancelled() is False


@pytest.mark.asyncio
async def test_lifespan_starts_assistant_with_journal_degraded(tmp_path, monkeypatch):
    # No VOYAGE_API_KEY -> build_journal_service() returns None; the assistant still
    # boots with both tools present (search_journal degrades to "unavailable").
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-journal")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.assistant_task is not None
        assert not app.state.assistant_task.done()
    assert app.state.assistant_task.done()
    assert app.state.assistant_task.cancelled() is False
