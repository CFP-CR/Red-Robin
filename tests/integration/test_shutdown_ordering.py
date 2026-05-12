"""Slice 10: shutdown ordering — tasks drain before adapters close.

The pre-Slice-10 order in lifespan's ``finally`` block was:
1. close adapters (httpx clients) → 2. drain background tasks → 3. cancel
promoter/summarizer → 4. close bus → 5. wait reducer → 6. stop rules watchdog.

That order means an in-flight ``_dispatch_to_cs_adapter()`` task scheduled
just before shutdown hits a CLOSED httpx client and fails spuriously.

The correct order:
1. Drain background tasks (5s timeout) — in-flight dispatches finish or get
   cleanly cancelled.
2. Close adapters (httpx clients).
3. Cancel promoter + summarizer loops.
4. Close bus (sentinel broadcast).
5. Wait reducer to drain.
6. Stop rules watchdog.

These tests pin that contract.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / ".redrobin"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.setenv("REDROBIN_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("REDROBIN_WORKING_MEMORY_PATH", str(tmp_path / "wm.json"))
    monkeypatch.setenv("REDROBIN_EPISODIC_PATH", str(tmp_path / "ep.db"))
    monkeypatch.setenv("REDROBIN_LONGTERM_PATH", str(tmp_path / "lt.db"))
    monkeypatch.setenv("REDROBIN_SUMMARY_PATH", str(tmp_path / "cs.db"))
    monkeypatch.setenv("REDROBIN_ALLOW_DEGRADED", "1")


@pytest.mark.asyncio
async def test_inflight_adapter_dispatch_runs_with_open_adapter(
    tmp_path: Path, monkeypatch
):
    """A long-running adapter task scheduled during operation must finish
    BEFORE the adapter's httpx client is closed.

    We install a fake CS adapter that records two facts when its
    ``dispatch_console_command`` runs:
    * the value of ``self._closed`` at the moment dispatch fired,
    * a flag set on completion.

    ``aclose()`` flips ``_closed = True``. If the shutdown order drains
    tasks BEFORE closing adapters (correct), the dispatch sees
    ``_closed=False``. If it closes adapters first (pre-Slice-10), the
    dispatch races and may see ``_closed=True``.
    """
    _setup_env(tmp_path, monkeypatch)

    from redrobin.api.app import create_app

    app = create_app()

    completed = asyncio.Event()
    saw_closed_at_dispatch: dict[str, bool | None] = {"value": None}

    class FakeAdapter:
        def __init__(self) -> None:
            self._closed = False

        async def dispatch_console_command(self, bid: str, command: str) -> None:
            # Yield once so the close hook gets a chance to run if the
            # order is wrong. Then record the adapter state and finish.
            await asyncio.sleep(0.5)
            saw_closed_at_dispatch["value"] = self._closed
            completed.set()

        async def aclose(self) -> None:
            self._closed = True

    async with app.router.lifespan_context(app):
        fake = FakeAdapter()
        app.state.cs_adapters["fake"] = fake

        async def slow_dispatch() -> None:
            await fake.dispatch_console_command("B-1", "whoami")

        # Schedule via the same tracker the verdict route + pipeline use.
        app.state.background_task_factory(slow_dispatch(), name="test_dispatch")
        # Trigger shutdown by exiting the context manager.

    # After lifespan exits, the task should have completed AND seen the
    # adapter still open. If shutdown closed adapters first (pre-Slice-10),
    # ``saw_closed_at_dispatch["value"]`` would be True.
    assert completed.is_set(), (
        "background task did not run to completion before lifespan exit — "
        "drain step may be missing or out of order"
    )
    assert saw_closed_at_dispatch["value"] is False, (
        "background task observed adapter already closed — "
        "Slice 10 reorder regressed (adapters closed before tasks drained)"
    )


@pytest.mark.asyncio
async def test_promoter_and_summarizer_cancel_on_shutdown(
    tmp_path: Path, monkeypatch
):
    """Promoter and summarizer are long-running loops — they SHOULD be
    cancelled promptly at shutdown (not drained), since they have no
    finite work to finish."""
    _setup_env(tmp_path, monkeypatch)

    from redrobin.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        promoter_task = getattr(app.state, "promoter_task", None)
        summarizer_task = getattr(app.state, "summarizer_task", None)

    # After lifespan exits, both should be done (cancelled).
    if promoter_task is not None:
        assert promoter_task.done()
    if summarizer_task is not None:
        assert summarizer_task.done()
