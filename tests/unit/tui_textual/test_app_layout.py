import pytest

from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_layout_has_named_regions():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        # The three panes, the topbar, and the reserved advisor dock all exist.
        for selector in ("#topbar", "#sessions", "#events", "#memory", "#advisor-dock"):
            assert app.query(selector), f"missing region {selector}"
        # Footer (Textual built-in) is present for keybinding discoverability.
        from textual.widgets import Footer

        assert app.query(Footer)


@pytest.mark.asyncio
async def test_advisor_dock_is_hidden():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        dock = app.query_one("#advisor-dock")
        assert dock.display is False  # reserved seam, not shown in Phase 1
