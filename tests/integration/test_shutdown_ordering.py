"""Shutdown ordering: background tasks drain, then the loops are torn down.

The lifespan ``finally`` block in :mod:`redrobin.api.app` tears subsystems
down in a fixed order:

1. Drain tracked fire-and-forget tasks (5s timeout, then cancel stragglers).
2. Cancel the L5 summariser loop.
3. Close the bus (sentinel broadcast to every subscriber).
4. Wait for the reducer to drain.
5. Wait for the assistant to drain (only started when the assistant is enabled).
6. Wait for the enricher to drain.
7. Close the dedup keys (a no-op close kept to match the helper convention).

These tests pin the loop half of that contract: every long-running task the
lifespan owns has finished by the time the context manager exits — the
summariser because step 2 cancels it, the reducer and enricher because the
step-3 sentinel ends their bus loops.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")


@pytest.mark.asyncio
async def test_lifespan_loops_are_finished_after_shutdown(tmp_path: Path, monkeypatch):
    """The summariser, reducer and enricher loops must all be done at teardown.

    The summariser is a long-running timer loop with no finite work to finish,
    so ``close_summarizer`` cancels it. The reducer and enricher are bus
    consumers, so ``close_bus``'s sentinel is what ends them and their close
    hooks await the drain. Either way, nothing may still be running once the
    lifespan context exits.
    """
    _setup_env(tmp_path, monkeypatch)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # Every helper that owns a loop publishes its task on app.state; a
        # missing attribute means a subsystem silently stopped starting.
        summarizer_task = app.state.summarizer_task
        reducer_task = app.state.reducer_task
        enricher_task = app.state.enricher_task
        assert not summarizer_task.done()

    assert summarizer_task.done()
    assert reducer_task.done()
    assert enricher_task.done()
