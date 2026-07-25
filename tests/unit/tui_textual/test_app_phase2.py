import pytest

from redrobin.tui.textual_ui.app import REGISTRY, RedRobinApp
from redrobin.tui.textual_ui.panels import MemoryPanel, SessionsTable


@pytest.mark.asyncio
async def test_snapshot_frame_populates_sessions_and_memory():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "working_memory_snapshot",
                "payload": {
                    "engagement_id": "e2e-verify",
                    "current_phase": "recon",
                    "sessions": [
                        {"beacon_id": "b1", "hostname": "H", "user": "u", "integrity": "High"}
                    ],
                    "findings": [{}, {}, {}],
                    "credentials": [{}],
                    "hosts": [{}],
                },
            },
        )
        assert app.query_one("#sessions-table", SessionsTable).row_count == 1
        assert "findings 3" in str(app.query_one("#memory-panel", MemoryPanel).content)
