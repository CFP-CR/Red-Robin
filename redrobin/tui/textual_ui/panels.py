"""Phase 2 panel widgets + pure render helpers for the Textual TUI.

``format_topbar`` and ``format_memory`` are pure (no running app) so they can be
unit-tested directly; the widgets are thin wrappers that just ``update`` with them.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable, Static

# DataTable cells take Rich styles (not CSS vars), so the concrete hexes live
# here; keep in sync with theme.tcss.
_SEV_HIGH = "#f0883e"
_MUTED = "#8b949e"
# Daemon-connection status colors — mirror $status-ok/$status-warn/$status-err in theme.tcss.
_STATUS_COLORS: dict[str, str] = {
    "connected": "#3fb950",
    "connecting": "#e3b341",
    "disconnected": "#f85149",
}
_SCOPE_OUT = "#f85149"  # mirror $scope-out in theme.tcss


def format_topbar(engagement: str, connection_state: str) -> Text:
    """Header line: 'Red Robin · <engagement> · ●<connection>'.

    16-color-safe: the connection state carries a text label alongside the dot, so
    meaning survives non-truecolor terminals and color-blindness."""
    line = Text()
    line.append("Red Robin", style="bold")
    line.append(f"  ·  {engagement or '-'}  ·  ", style=_MUTED)
    line.append("●", style=_STATUS_COLORS.get(connection_state, _MUTED))
    line.append(f" {connection_state}", style=_MUTED)
    return line


class TopBar(Static):
    """Docked header: engagement id + daemon-connection status."""

    def update_status(self, engagement: str, connection_state: str) -> None:
        self.update(format_topbar(engagement, connection_state))


class SessionsTable(DataTable):
    """Active-beacon table: BID / HOST / USER / INT."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("BID", "HOST", "USER", "INT")

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not self.columns:  # not yet mounted / columns not added
            return
        self.clear()
        for s in snapshot.get("sessions") or []:
            integ = str(s.get("integrity") or "-")
            integ_cell = Text(integ, style=_SEV_HIGH) if integ == "High" else Text(integ)
            self.add_row(
                str(s.get("beacon_id") or "?"),
                str(s.get("hostname") or "-"),
                str(s.get("user") or "-"),
                integ_cell,
            )


def format_memory(snapshot: dict[str, Any]) -> Text:
    """Working-memory summary as Rich Text: phase, scope (out-of-scope in red), counts.
    Same text the plain renderer produced; out-of-scope now styled with $scope-out."""
    in_scope = snapshot.get("scope_in_scope") or []
    out_scope = snapshot.get("scope_out_of_scope") or []
    phase = snapshot.get("current_phase") or "-"
    hosts = len(snapshot.get("hosts") or [])
    creds = len(snapshot.get("credentials") or [])
    finds = len(snapshot.get("findings") or [])
    text = Text()
    text.append(f"phase   {phase}\n\n")
    text.append("SCOPE\n", style=_MUTED)
    text.append(f"  in   {', '.join(in_scope) if in_scope else '(none)'}\n")
    text.append("  out  ")
    text.append(f"{', '.join(out_scope) if out_scope else '(none)'}\n", style=_SCOPE_OUT)
    text.append("\n")
    text.append(f"hosts {hosts}   creds {creds}   findings {finds}")
    return text


class MemoryPanel(Static):
    """Working-memory summary: phase, scope (in/out), and counts."""

    can_focus = True

    def update_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.update(format_memory(snapshot))
