import pytest

from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_tab_cycles_the_three_panes():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "sessions-table"
        order = []
        for _ in range(3):
            await pilot.press("tab")
            await pilot.pause()
            order.append(app.focused.id)
        assert order == ["events-log", "memory-panel", "sessions-table"]


@pytest.mark.asyncio
async def test_shift_tab_reverses():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == "memory-panel"


@pytest.mark.asyncio
async def test_question_mark_opens_help():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        from textual.widgets import Static

        body = app.screen.query_one("#help-body", Static)
        text = str(body.content)
        assert "Inspector" in text
        assert "Command palette" in text


@pytest.mark.asyncio
async def test_help_escape_closes():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "HelpScreen"
