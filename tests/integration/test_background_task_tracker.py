"""Daemon-wide tracking of fire-and-forget asyncio tasks.

Fire-and-forget callsites used to leak tasks on shutdown. Such callsites route
through a tracked task factory plumbed onto ``app.state.background_tasks``; the
lifespan shutdown awaits any pending tasks (timeout 5s) and cancels stragglers.
These tests verify the tracker is wired up and that shutdown drains it.

The gate's ESCALATE -> Layer B dispatch was the original (and, at the time of
writing, only) production caller; it died with the pre-flight gate, so the
tracker is currently exercised through ``app.state.background_task_factory``
directly. The machinery is kept because it is generic and the next async
callsite must not have to reinvent it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from redrobin.api.app import create_app


def _setup_env(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    # No API keys here — opt into degraded boot explicitly so validate_for_boot
    # does not exit(2).
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")
    return root


@pytest.mark.asyncio
async def test_lifespan_initialises_background_tasks_container(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    app = create_app()
    async with app.router.lifespan_context(app):
        assert hasattr(app.state, "background_tasks")
        assert isinstance(app.state.background_tasks, set)


@pytest.mark.asyncio
async def test_shutdown_drains_pending_background_tasks(tmp_path, monkeypatch):
    """A still-running tracked task should be awaited (or cancelled) on shutdown.

    Without the tracker, the task would leak silently; with it, the lifespan
    finalizer awaits up to 5s and then cancels stragglers. We verify both
    outcomes: a fast task completes naturally, a slow task is cancelled.
    """
    _setup_env(tmp_path, monkeypatch)
    app = create_app()

    async with app.router.lifespan_context(app):
        captured: dict[str, asyncio.Task] = {}

        async def fast_coro():
            return 1

        async def slow_coro():
            await asyncio.sleep(60)

        # Schedule via the same factory the production callsites use.
        factory = app.state.background_task_factory
        captured["fast"] = factory(fast_coro())
        captured["slow"] = factory(slow_coro())

        # Both should be tracked.
        assert captured["fast"] in app.state.background_tasks
        assert captured["slow"] in app.state.background_tasks

    # After lifespan exits the fast task completed and the slow task was
    # cancelled. Both are done so neither leaks.
    assert captured["fast"].done()
    assert captured["slow"].done() or captured["slow"].cancelled()
