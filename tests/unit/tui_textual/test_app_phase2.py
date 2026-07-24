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


@pytest.mark.asyncio
async def test_verdict_frames_render_and_layer_b_corrects_in_place():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "c1",
                    "verdict": "ESCALATE",
                    "command": "seatbelt",
                    "source": "layer_a",
                },
            },
        )
        REGISTRY.dispatch(
            target=app,
            frame={
                "type": "opsec.verdict",
                "payload": {
                    "correlation_id": "c1",
                    "verdict": "PASS",
                    "previous_verdict": "ESCALATE",
                    "source": "layer_b",
                },
            },
        )
        # Corrected in place → still one event, now PASS. The verdict STREAM display
        # was superseded by the Chat pane (#28); the app.events reactive (which feeds
        # the topbar count) still applies Layer-B corrections in place.
        assert len(app.events) == 1
        assert app.events[0]["verdict"] == "PASS"
