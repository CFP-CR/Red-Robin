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
