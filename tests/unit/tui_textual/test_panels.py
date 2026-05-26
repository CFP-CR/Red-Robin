import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from redrobin.tui.textual_ui import panels
from redrobin.tui.textual_ui.panels import EventsLog, MemoryPanel, SessionsTable


def test_format_verdict_is_text_with_command_and_source():
    out = panels.format_verdict(
        {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass"}
    )
    assert isinstance(out, Text)
    plain = out.plain
    assert "BLOCK" in plain
    assert "mimikatz" in plain
    assert "layer_a" in plain
    assert "lsass" in plain


def test_format_verdict_no_reason_omits_dash():
    out = panels.format_verdict({"verdict": "PASS", "command": "whoami", "source": "layer_b"})
    assert "—" not in out.plain


def test_apply_verdict_appends_layer_a():
    events = panels.apply_verdict(
        [], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a"}
    )
    assert len(events) == 1
    assert events[0]["verdict"] == "PASS"


def test_apply_verdict_layer_b_corrects_in_place():
    events = panels.apply_verdict(
        [], {"correlation_id": "c1", "verdict": "PASS", "source": "layer_a", "command": "mimikatz"}
    )
    events = panels.apply_verdict(
        events,
        {
            "correlation_id": "c1",
            "verdict": "BLOCK",
            "previous_verdict": "PASS",
            "source": "layer_b",
        },
    )
    assert len(events) == 1  # corrected in place, not appended
    assert events[0]["verdict"] == "BLOCK"
    assert events[0]["previous_verdict"] == "PASS"
    assert events[0]["command"] == "mimikatz"  # original fields preserved


def test_apply_verdict_layer_b_no_match_appends():
    events = panels.apply_verdict(
        [], {"correlation_id": "zzz", "verdict": "BLOCK", "source": "layer_b"}
    )
    assert len(events) == 1


def test_apply_verdict_caps_at_500():
    events: list[dict] = []
    for i in range(520):
        events = panels.apply_verdict(
            events, {"correlation_id": str(i), "verdict": "PASS", "source": "layer_a"}
        )
    assert len(events) == 500
    assert events[-1]["correlation_id"] == "519"


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


class _LogApp(App):
    def compose(self) -> ComposeResult:
        yield EventsLog(id="log")


class _MemApp(App):
    def compose(self) -> ComposeResult:
        yield MemoryPanel(id="mem")


@pytest.mark.asyncio
async def test_events_log_writes_one_line_per_event():
    app = _LogApp()
    async with app.run_test():
        log = app.query_one("#log", EventsLog)
        log.render_events(
            [
                {"verdict": "PASS", "command": "whoami", "source": "layer_a"},
                {"verdict": "BLOCK", "command": "mimikatz", "source": "layer_a", "reason": "lsass"},
            ]
        )
        assert len(log.lines) == 2


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


def test_verdict_counts_tallies_known_verdicts_only():
    events = [
        {"verdict": "BLOCK"},
        {"verdict": "BLOCK"},
        {"verdict": "PASS"},
        {"verdict": "WARN"},
        {"verdict": "ESCALATE"},
        {"verdict": "WAT"},  # unknown → ignored
        {},  # no verdict → ignored
    ]
    counts = panels.verdict_counts(events)
    assert counts == {"BLOCK": 2, "WARN": 1, "PASS": 1, "ESCALATE": 1}


def test_format_topbar_is_16color_safe_text():
    out = panels.format_topbar(
        "e2e-verify", "connected", {"BLOCK": 3, "WARN": 1, "PASS": 12, "ESCALATE": 0}
    )
    assert isinstance(out, Text)
    plain = out.plain
    # Every colored token also carries a text label (survives no-color terminals).
    assert "e2e-verify" in plain
    assert "connected" in plain
    assert "3 BLOCK" in plain
    assert "1 WARN" in plain
    assert "12 PASS" in plain
    assert "0 ESCALATE" in plain


def test_format_topbar_blank_engagement_shows_dash():
    out = panels.format_topbar(
        "", "disconnected", {"BLOCK": 0, "WARN": 0, "PASS": 0, "ESCALATE": 0}
    )
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
        bar.update_status("acme", "connected", {"BLOCK": 1, "WARN": 0, "PASS": 0, "ESCALATE": 0})
        content = str(bar.content)
        assert "acme" in content
        assert "1 BLOCK" in content
