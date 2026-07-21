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
        # The Chat scrollback (#chat-log) is can_focus=False, so only its Input is a
        # tab stop: sessions-table → chat-input → memory-panel (wrapping).
        assert order == ["chat-input", "memory-panel", "sessions-table"]


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


@pytest.mark.asyncio
async def test_command_specs_open_inspector():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        from redrobin.tui.textual_ui.commands import command_specs

        specs = command_specs(app)
        names = [name for name, _, _ in specs]
        assert any("Inspector" in n for n in names)
        assert any("Sessions" in n for n in names)
        assert any("Working Memory" in n for n in names)
        runnable = next(cmd for name, cmd, _ in specs if "Inspector" in name)
        runnable()
        await pilot.pause()
        assert app.screen.__class__.__name__ == "InspectorScreen"


@pytest.mark.asyncio
async def test_command_specs_focus_a_pane():
    app = RedRobinApp(start_worker=False)
    async with app.run_test() as pilot:
        from redrobin.tui.textual_ui.commands import command_specs

        runnable = next(cmd for name, cmd, _ in command_specs(app) if "Chat" in name)
        runnable()
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "chat-input"


def test_app_registers_red_robin_command_provider():
    from redrobin.tui.textual_ui.app import RedRobinApp
    from redrobin.tui.textual_ui.commands import RedRobinCommands

    assert RedRobinCommands in RedRobinApp.COMMANDS
