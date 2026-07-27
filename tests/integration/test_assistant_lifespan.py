"""The assistant boots (degraded, no API key) as a lifespan task, and settings
carry the assistant knobs. Mirrors test_enricher.py::test_lifespan_starts_enricher_task."""

from __future__ import annotations

import logging

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


@pytest.mark.asyncio
async def test_shutdown_error_line_names_the_task_that_crashed(tmp_path, monkeypatch, caplog):
    # start_assistant's close hook awaits the assistant AND the chat recorder under one
    # asyncio.wait, and its error log is the ONLY place a LOOP-level crash in either surfaces
    # (both loops swallow per-event exceptions). One line for two tasks is useless unless it
    # says which one, so it must carry task.get_name().
    from redrobin.application.agents.assistant.assistant import Assistant

    async def _crash(self) -> None:
        if self.on_started is not None:
            self.on_started()  # let start_assistant's await return before we die
        raise RuntimeError("loop-level crash")

    monkeypatch.setattr(Assistant, "run", _crash)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-crash")
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "1")  # so the chat recorder runs too
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    app = create_app()
    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            pass

    assert "task 'assistant' ended with an exception" in caplog.text
    assert "loop-level crash" in caplog.text  # the traceback came along


def test_settings_expose_proactive_knobs(monkeypatch):
    monkeypatch.delenv("REDROBIN_ASSISTANT_LULL_SEC", raising=False)
    monkeypatch.delenv("REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC", raising=False)
    from redrobin.application.config import Settings

    s = Settings()
    assert s.assistant_lull_sec == 300.0
    assert s.assistant_proactive_min_interval_sec == 600.0


def test_settings_proactive_knobs_override(monkeypatch):
    monkeypatch.setenv("REDROBIN_ASSISTANT_LULL_SEC", "45")
    monkeypatch.setenv("REDROBIN_ASSISTANT_PROACTIVE_MIN_INTERVAL_SEC", "90")
    from redrobin.application.config import Settings

    s = Settings()
    assert s.assistant_lull_sec == 45.0
    assert s.assistant_proactive_min_interval_sec == 90.0


def test_settings_proactive_knobs_reject_non_positive(monkeypatch):
    import pytest as _pytest

    monkeypatch.setenv("REDROBIN_ASSISTANT_LULL_SEC", "0")
    from redrobin.application.config import Settings

    with _pytest.raises(Exception) as exc:
        Settings()
    assert "> 0" in str(exc.value) or "assistant_lull_sec" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_journal_built_only_when_assistant_enabled(tmp_path, monkeypatch):
    # The journal warm (a full-backlog embed) must not run when the assistant is
    # disabled — otherwise a boot with a VOYAGE key pays that cost for an off feature.
    import redrobin.api.lifespan_helpers as lh

    calls: list[bool] = []

    async def _spy():
        calls.append(True)
        return None

    monkeypatch.setattr(lh, "build_journal_service", _spy)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-gate")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    # disabled (default) -> journal factory NOT called
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    app = create_app()
    async with app.router.lifespan_context(app):
        pass
    assert calls == []

    # enabled -> journal factory IS called (returns None, degraded — still boots)
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "1")
    app2 = create_app()
    async with app2.router.lifespan_context(app2):
        pass
    assert calls == [True]


@pytest.mark.asyncio
async def test_kb_built_only_when_assistant_enabled(tmp_path, monkeypatch):
    # The KB store load (reads the whole kb-index) must not run when the assistant is
    # disabled — same rationale as the journal warm: no boot cost (and no delay to the
    # C2 adapters started after us) for an off feature.
    import redrobin.api.lifespan_helpers as lh

    calls: list[bool] = []

    def _spy():
        calls.append(True)
        return None

    monkeypatch.setattr(lh, "build_kb_service", _spy)
    monkeypatch.setenv("REDROBIN_ROOT", str(tmp_path))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    monkeypatch.delenv("REDROBIN_ENGAGEMENT_SLUG", raising=False)
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_ENGAGEMENT_ID", "eng-kbgate")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.api.app import create_app

    # disabled (default) -> kb factory NOT called
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "0")
    app = create_app()
    async with app.router.lifespan_context(app):
        pass
    assert calls == []

    # enabled -> kb factory IS called (returns None, degraded — still boots)
    monkeypatch.setenv("REDROBIN_ASSISTANT_ENABLED", "1")
    app2 = create_app()
    async with app2.router.lifespan_context(app2):
        pass
    assert calls == [True]
