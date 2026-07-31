import pytest

from redrobin.tui.textual_ui.app import RedRobinApp, REGISTRY


@pytest.mark.asyncio
async def test_snapshot_frame_updates_state_and_topbar():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {"engagement_id": "e2e-verify", "sessions": [], "findings": []},
            },
        )
        # No manual repaint call — setting the reactive is enough.
        assert app.snapshot.get("engagement_id") == "e2e-verify"
        from textual.widgets import Static

        topbar = app.query_one("#topbar", Static)
        assert "e2e-verify" in str(topbar.content)  # Static exposes .content (NOT .renderable)


@pytest.mark.asyncio
async def test_app_has_no_events_reactive_and_no_verdict_handler():
    """The pre-flight gate is gone: nothing publishes ``opsec.verdict``, so the
    frame handler and the ``events`` reactive it fed are both removed."""
    assert not REGISTRY.has_handler("opsec.verdict")
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        assert not hasattr(app, "events")
