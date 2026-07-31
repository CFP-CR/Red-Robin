"""Slice 8: lifespan decomposition.

These tests pin two contracts:

1. Every ``start_*`` helper module-level name is importable. If a helper
   gets renamed or accidentally deleted the import line fails before any
   test body runs, which is exactly the signal we want.
2. The lifespan boot path still works end-to-end after the
   monolithic body was refactored into helpers. Pure-refactor regression
   net for Slice 8 — no behavioral change allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from redrobin.api.lifespan_helpers import (
    start_assistant,
    start_dedup,
    start_engagement,
    start_enricher,
    start_event_bus,
    start_l5_summarizer,
    start_working_memory,
)


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")


def test_each_helper_is_importable():
    """If any helper doesn't exist, this test catches it immediately."""
    for fn in (
        start_event_bus,
        start_engagement,
        start_dedup,
        start_working_memory,
        start_enricher,
        start_assistant,
        start_l5_summarizer,
    ):
        assert callable(fn)


@pytest.mark.asyncio
async def test_start_event_bus_registers_on_app_state(tmp_path: Path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    from fastapi import FastAPI

    from redrobin.application.config import Settings

    app = FastAPI()
    settings = Settings()
    bus, close = await start_event_bus(app, settings)
    assert app.state.event_bus is bus
    assert close is not None
    await close()


@pytest.mark.asyncio
async def test_lifespan_still_works_end_to_end(tmp_path: Path, monkeypatch):
    """Regression test: existing lifespan boot path still works after decomposition.

    No behavioral change is allowed — every ``app.state.*`` attribute the
    old monolithic lifespan exposed must still be present after the
    refactor.
    """
    _setup_env(tmp_path, monkeypatch)
    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.event_bus is not None
        assert app.state.working_memory_repo is not None
        assert app.state.episodic_repo is not None
        assert app.state.longterm_repo is not None
        assert app.state.summary_repo is not None
        assert hasattr(app.state, "summarizer_task")
        assert hasattr(app.state, "reducer_task")
        assert hasattr(app.state, "enricher_task")
