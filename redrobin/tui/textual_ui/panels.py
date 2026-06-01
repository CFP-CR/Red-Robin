"""Phase 2 panel widgets + pure render/correlation helpers for the Textual TUI.

``format_verdict`` and ``apply_verdict`` are pure (no running app) so they can
be unit-tested directly — porting the proven logic from the legacy
``redrobin/tui/app.py:_format_event_line`` and ``redrobin/tui/state.py:update_verdict``.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import DataTable, RichLog, Static

# Mirror the $verdict-* tokens in theme.tcss. RichLog/DataTable cells take Rich
# styles (not CSS vars), so the concrete hexes live here; keep in sync with theme.tcss.
VERDICT_COLORS: dict[str, str] = {
    "BLOCK": "#f85149",
    "WARN": "#e3b341",
    "PASS": "#3fb950",
    "ESCALATE": "#58a6ff",
}
_SEV_HIGH = "#f0883e"
_MUTED = "#8b949e"
# Daemon-connection status colors — mirror $status-ok/$status-warn/$status-err in theme.tcss.
_STATUS_COLORS: dict[str, str] = {
    "connected": "#3fb950",
    "connecting": "#e3b341",
    "disconnected": "#f85149",
}
_SCOPE_OUT = "#f85149"  # mirror $scope-out in theme.tcss
_MAX_EVENTS = 500


def format_verdict(payload: dict[str, Any]) -> Text:
    """One color-coded line: '<VERDICT> <command>  (<source>) — <reason>'.
    No raw JSON (fixes #148)."""
    verdict = str(payload.get("verdict", "?"))
    cmd = str(payload.get("command", ""))
    source = str(payload.get("source", "?"))
    reason = str(payload.get("reason", ""))
    color = VERDICT_COLORS.get(verdict, "white")
    line = Text()
    line.append(f"{verdict:<8}", style=f"bold {color}")
    line.append(f" {cmd}  ")
    line.append(f"({source})", style=_MUTED)
    if reason:
        line.append(f" — {reason}", style=_MUTED)
    return line


def verdict_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Tally events by verdict action (only the four known actions are counted)."""
    counts = {"BLOCK": 0, "WARN": 0, "PASS": 0, "ESCALATE": 0}
    for event in events:
        verdict = event.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def format_topbar(engagement: str, connection_state: str, counts: dict[str, int]) -> Text:
    """Header line: 'Red Robin · <engagement> · ●<connection> · <verdict counts>'.

    16-color-safe: the connection state and each verdict carry a text label + weight,
    so meaning survives non-truecolor terminals and color-blindness."""
    line = Text()
    line.append("Red Robin", style="bold")
    line.append(f"  ·  {engagement or '-'}  ·  ", style=_MUTED)
    line.append("●", style=_STATUS_COLORS.get(connection_state, _MUTED))
    line.append(f" {connection_state}", style=_MUTED)
    for verdict in ("BLOCK", "WARN", "PASS", "ESCALATE"):
        line.append("   ")
        line.append(f"{counts.get(verdict, 0)} {verdict}", style=f"bold {VERDICT_COLORS[verdict]}")
    return line


def apply_verdict(events: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a NEW events list with the verdict applied. A Layer B correction
    (``source == "layer_b"`` with a matching ``correlation_id``) updates the most
    recent matching entry in place; otherwise the verdict is appended. Capped at
    the last ``_MAX_EVENTS``. Reassignment (not mutation) so the reactive watcher
    fires. Ports ``redrobin/tui/state.py:update_verdict``."""
    new = list(events)
    source = payload.get("source", "layer_a")
    corr = payload.get("correlation_id")
    if source == "layer_b" and corr is not None:
        for i in range(len(new) - 1, -1, -1):
            if new[i].get("correlation_id") == corr:
                new[i] = {**new[i], **payload}
                return new[-_MAX_EVENTS:]
    new.append(payload)
    return new[-_MAX_EVENTS:]


class TopBar(Static):
    """Docked header: engagement id + daemon-connection status + verdict counts."""

    def update_status(self, engagement: str, connection_state: str, counts: dict[str, int]) -> None:
        self.update(format_topbar(engagement, connection_state, counts))


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


class EventsLog(RichLog):
    """Scrollable, color-coded verdict/event log. Re-rendered wholesale from the
    events list (so Layer-B in-place corrections appear correctly)."""

    def render_events(self, events: list[dict[str, Any]]) -> None:
        self.clear()
        for payload in events:
            self.write(format_verdict(payload))


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
