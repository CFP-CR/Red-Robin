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
async def test_verdict_frame_appends_event():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a"},
            },
        )
        assert len(app.events) == 1
        assert app.events[0]["verdict"] == "BLOCK"
