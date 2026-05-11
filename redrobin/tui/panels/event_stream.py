"""redrobin/tui/panels/event_stream.py — Center pane: event flow."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventEntry:
    timestamp: str  # ISO
    severity: str  # INFO / WARN / DANGER / PASS
    text: str


def render_event_stream(entries: list[EventEntry]) -> str:
    """Render the event stream as a scrollback log.

    Format:
        [14:32:01] [DANGER] mimikatz logonpasswords — lsass touch, blocked
        [14:32:15] [WARN]   net group "Domain Admins" — AD recon
    """
    lines: list[str] = []
    for entry in entries:
        if "T" in entry.timestamp:
            time_part = entry.timestamp.split("T")[-1]
        else:
            time_part = entry.timestamp
        # Strip fractional seconds and trailing tz suffix ('Z' or '+HH:MM')
        time_part = time_part.split(".")[0]
        for sep in ("Z", "+", "-"):
            if sep in time_part:
                time_part = time_part.split(sep)[0]
                break
        ts_short = time_part
        lines.append(f"[{ts_short}] [{entry.severity:<6}] {entry.text}")
    return "\n".join(lines) if lines else "(no events yet)"
