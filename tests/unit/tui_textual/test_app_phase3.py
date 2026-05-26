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


def test_is_too_small_boundaries():
    from redrobin.tui.textual_ui.app import is_too_small

    assert is_too_small(79, 24) is True
    assert is_too_small(80, 23) is True
    assert is_too_small(80, 24) is False
    assert is_too_small(120, 40) is False


@pytest.mark.asyncio
async def test_resize_hint_shows_when_too_small():
    app = RedRobinApp(start_worker=False)
    async with app.run_test(size=(40, 20)):
        assert app.query_one("#resize-hint").display is True
        assert app.query_one("#main").display is False


@pytest.mark.asyncio
async def test_resize_hint_hidden_when_big_enough():
    app = RedRobinApp(start_worker=False)
    async with app.run_test(size=(100, 30)):
        assert app.query_one("#resize-hint").display is False
        assert app.query_one("#main").display is True


@pytest.mark.asyncio
async def test_events_log_long_reason_does_not_overflow_horizontally():
    # A long verdict reason must wrap to the pane width — no horizontal scrollbar.
    # RichLog defaults to wrap=False + min_width=78 (forces a 78-wide canvas that
    # overflows a narrower pane); we set wrap=True + min_width=0.
    from redrobin.tui.textual_ui.panels import EventsLog, format_verdict

    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        log = app.query_one("#events-log", EventsLog)
        assert log.wrap is True
        log.write(
            format_verdict(
                {
                    "verdict": "BLOCK",
                    "command": "mimikatz sekurlsa::logonpasswords",
                    "source": "layer_a",
                    "reason": (
                        "lsass touch + plaintext credential matches T1003.001. Sysmon EID 10 "
                        "and most EDRs (CrowdStrike, Defender, SentinelOne) flag this signature."
                    ),
                }
            )
        )
        await pilot.pause()
        # virtual width must not exceed the visible pane width → no horizontal scroll.
        assert log.virtual_size.width <= log.size.width
