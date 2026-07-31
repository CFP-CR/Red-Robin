import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from redrobin.tui.textual_ui import panels
from redrobin.tui.textual_ui.panels import MemoryPanel, SessionsTable


class _SessApp(App):
    def compose(self) -> ComposeResult:
        yield SessionsTable(id="t")


@pytest.mark.asyncio
async def test_sessions_table_populates_rows():
    app = _SessApp()
    async with app.run_test():
        table = app.query_one("#t", SessionsTable)
        table.update_from_snapshot(
            {
                "sessions": [
                    {
                        "beacon_id": "2047462324",
                        "hostname": "EC2AMAZ",
                        "user": "Administrator",
                        "integrity": "High",
                    },
                    {
                        "beacon_id": "1200206136",
                        "hostname": "JCSP",
                        "user": "jcspi",
                        "integrity": "Medium",
                    },
                ]
            }
        )
        assert table.row_count == 2
        assert str(table.get_cell_at((0, 0))) == "2047462324"
        assert str(table.get_cell_at((0, 3))) == "High"


@pytest.mark.asyncio
async def test_sessions_table_clears_on_reupdate():
    app = _SessApp()
    async with app.run_test():
        table = app.query_one("#t", SessionsTable)
        table.update_from_snapshot(
            {"sessions": [{"beacon_id": "a", "hostname": "h", "user": "u", "integrity": "Low"}]}
        )
        table.update_from_snapshot({"sessions": []})
        assert table.row_count == 0


class _MemApp(App):
    def compose(self) -> ComposeResult:
        yield MemoryPanel(id="mem")


@pytest.mark.asyncio
async def test_memory_panel_shows_phase_scope_counts():
    app = _MemApp()
    async with app.run_test():
        mem = app.query_one("#mem", MemoryPanel)
        mem.update_from_snapshot(
            {
                "current_phase": "recon",
                "scope_in_scope": ["EC2AMAZ", "10.100.0.0/24"],
                "scope_out_of_scope": ["10.99.0.0/16"],
                "hosts": [{}],
                "credentials": [{}],
                "findings": [{}, {}],
            }
        )
        content = str(mem.content)
        assert "recon" in content
        assert "EC2AMAZ" in content
        assert "10.99.0.0/16" in content
        assert "findings 2" in content


@pytest.mark.asyncio
async def test_memory_panel_empty_scope_is_graceful():
    app = _MemApp()
    async with app.run_test():
        mem = app.query_one("#mem", MemoryPanel)
        mem.update_from_snapshot({})
        content = str(mem.content)
        assert "(none)" in content


def test_format_topbar_is_16color_safe_text():
    out = panels.format_topbar("e2e-verify", "connected")
    assert isinstance(out, Text)
    plain = out.plain
    # Every colored token also carries a text label (survives no-color terminals).
    assert "e2e-verify" in plain
    assert "connected" in plain


def test_format_topbar_blank_engagement_shows_dash():
    out = panels.format_topbar("", "disconnected")
    # Blank engagement falls back to "-" (rendered between the middle-dot separators).
    assert " - " in out.plain


@pytest.mark.asyncio
async def test_topbar_widget_renders_status():
    class _BarApp(App):
        def compose(self) -> ComposeResult:
            yield panels.TopBar("Red Robin", id="bar")

    app = _BarApp()
    async with app.run_test():
        bar = app.query_one("#bar", panels.TopBar)
        bar.update_status("acme", "connected")
        content = str(bar.content)
        assert "acme" in content
        assert "connected" in content


def test_topbar_has_no_verdict_counters():
    from redrobin.tui.textual_ui.panels import format_topbar

    s = format_topbar("vantage", "connected").plain
    assert "Red Robin" in s
    assert "vantage" in s
    assert "connected" in s
    for word in ("BLOCK", "PASS", "ESCALATE"):
        assert word not in s


def test_format_memory_marks_out_of_scope_red():
    out = panels.format_memory(
        {
            "current_phase": "recon",
            "scope_in_scope": ["EC2AMAZ", "10.100.0.0/24"],
            "scope_out_of_scope": ["10.99.0.0/16"],
            "hosts": [{}],
            "credentials": [{}],
            "findings": [{}, {}],
        }
    )
    assert isinstance(out, Text)
    plain = out.plain
    # Text content is preserved (same fields the plain renderer showed).
    assert "recon" in plain
    assert "EC2AMAZ" in plain
    assert "10.99.0.0/16" in plain
    assert "findings 2" in plain
    # The out-of-scope value carries the $scope-out red style.
    assert any(panels._SCOPE_OUT in str(span.style) for span in out.spans)


def test_format_memory_empty_scope_is_graceful():
    out = panels.format_memory({})
    assert "(none)" in out.plain
