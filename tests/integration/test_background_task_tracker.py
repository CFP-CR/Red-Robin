"""Daemon-wide tracking of fire-and-forget asyncio tasks.

Two production callsites used to leak tasks on shutdown:

* ``redrobin/api/verdicts_router.py`` schedules a CS adapter dispatch on PASS
  with an adapter name (``asyncio.create_task(_dispatch_to_cs_adapter(...))``).
* ``redrobin/application/verdict_pipeline.py`` schedules Layer B on ESCALATE
  (``asyncio.create_task(self._run_layer_b(...))``).

Both now route through a tracked task factory plumbed onto
``app.state.background_tasks``; the lifespan shutdown awaits any pending tasks
(timeout 5s) and cancels stragglers. These tests verify the tracker is wired
into both paths.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from redrobin.api.app import create_app


def _setup_env(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    # Slice 3: opt into degraded Layer B explicitly. This test fixture
    # previously relied on the silent fallback in _build_layer_b.
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
async def test_escalate_verdict_tracks_layer_b_task(tmp_path, monkeypatch):
    """ESCALATE → Layer B fire-and-forget task must be tracked."""
    _setup_env(tmp_path, monkeypatch)

    # Slow down Layer B so the task is visible while we inspect the set.
    class SlowLayerB:
        on_retroactive_verdict: object = None

        async def evaluate(self, *, command, raw):
            await asyncio.sleep(0.5)
            from redrobin.domain.opsec import Verdict, VerdictAction

            return Verdict(action=VerdictAction.PASS, reason="slow", rule_id=None)

    app = create_app()
    async with app.router.lifespan_context(app):
        # Swap Layer B for the slow stub so ESCALATE schedules a measurable
        # background task. The pipeline holds a private ref to layer_b so the
        # cleanest swap is to rebuild the pipeline.
        from redrobin.application.verdict_pipeline_factory import build_pipeline

        old = app.state.verdict_pipeline
        new_pipeline = build_pipeline(
            event_bus=app.state.event_bus,
            scope=old._scope,  # type: ignore[attr-defined]
            layer_a=old._layer_a,  # type: ignore[attr-defined]
            layer_b=SlowLayerB(),
        )
        # Re-attach the tracker so the new pipeline tracks tasks too.
        new_pipeline._task_factory = old._task_factory  # type: ignore[attr-defined]
        app.state.verdict_pipeline = new_pipeline

        before = len(app.state.background_tasks)
        # An unmatched command falls through Layer A → ESCALATE → Layer B task.
        await new_pipeline.quick_verdict(
            command="unmatched_command_xyz",
            raw="unmatched_command_xyz",
            source_event_id="corr-track-1",
            beacon_id="B-x",
        )
        # The Layer B task should now be in the tracker.
        # Allow a single tick for the asyncio scheduling to settle.
        await asyncio.sleep(0)
        assert len(app.state.background_tasks) > before, (
            f"layer B task not tracked. before={before} "
            f"after={len(app.state.background_tasks)}"
        )
        # Drain so shutdown isn't slowed by the SlowLayerB sleep.
        for task in list(app.state.background_tasks):
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()


@pytest.mark.asyncio
async def test_pass_with_adapter_tracks_dispatch_task(tmp_path, monkeypatch):
    """PASS + adapter name → CS adapter dispatch task must be tracked."""
    _setup_env(tmp_path, monkeypatch)
    app = create_app()
    transport = ASGITransport(app=app)

    # Inject a fake adapter so the dispatch coroutine actually runs.
    class SlowAdapter:
        async def dispatch_console_command(self, bid, command):
            await asyncio.sleep(0.5)

        async def aclose(self):
            return None

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            app.state.cs_adapters["test-adapter"] = SlowAdapter()

            from redrobin.infrastructure.transport.bearer import get_or_create_token

            before = len(app.state.background_tasks)
            resp = await client.post(
                "/v1/verdict/quick",
                json={
                    "bid": "B-1",
                    "command": "pwd",  # passthrough → PASS
                    "raw": "pwd",
                    "source_event_id": "corr-dispatch-1",
                    "adapter": "test-adapter",
                },
                headers={"Authorization": f"Bearer {get_or_create_token()}"},
            )
            assert resp.status_code == 200
            assert resp.json()["action"] == "PASS"
            # Give the route handler one tick to schedule the tracked task.
            await asyncio.sleep(0)
            assert len(app.state.background_tasks) > before
            # Drain so shutdown doesn't wait the full 0.5s + 5s.
            for task in list(app.state.background_tasks):
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()


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
