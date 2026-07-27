"""Shutdown ordering: background tasks drain, then loops are cancelled.

The lifespan ``finally`` block tears subsystems down in a fixed order:
1. Drain tracked fire-and-forget tasks (5s timeout) — in-flight work
   finishes or is cleanly cancelled.
2. Cancel the summariser loop.
3. Close the bus (sentinel broadcast).
4. Wait for the reducer to drain.

These tests pin the loop-cancellation half of that contract.
"""

from __future__ import annotations

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
async def test_promoter_and_summarizer_cancel_on_shutdown(tmp_path: Path, monkeypatch):
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
