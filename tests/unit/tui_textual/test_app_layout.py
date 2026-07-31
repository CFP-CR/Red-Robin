import pytest

from redrobin.tui.textual_ui.app import RedRobinApp


@pytest.mark.asyncio
async def test_layout_has_named_regions():
    app = RedRobinApp(start_worker=False)
    async with app.run_test():
        # The three panes and the topbar all exist.
        for selector in ("#topbar", "#sessions", "#events", "#memory"):
            assert app.query(selector), f"missing region {selector}"
        # Footer (Textual built-in) is present for keybinding discoverability.
        from textual.widgets import Footer

        assert app.query(Footer)
