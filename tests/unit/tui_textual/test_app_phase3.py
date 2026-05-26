import pytest

from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp
from redrobin.tui.textual_ui.panels import TopBar


@pytest.mark.asyncio
async def test_topbar_shows_engagement_and_verdict_counts():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {"engagement_id": "e2e-verify", "sessions": [], "findings": []},
            },
        )
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a"},
            },
        )
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {"verdict": "PASS", "command": "whoami", "source": "layer_a"},
            },
        )
        content = str(app.query_one("#topbar", TopBar).content)
        assert "e2e-verify" in content
        assert "1 BLOCK" in content
        assert "1 PASS" in content


@pytest.mark.asyncio
async def test_topbar_reflects_connection_state():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        app.connection_state = "connected"
        await pilot.pause()
        assert "connected" in str(app.query_one("#topbar", TopBar).content)
