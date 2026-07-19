"""AI Advisor dock handler — renders advisor.recommendation frames.

`tui_ws` forwards every bus event to the TUI, so the assistant's
`advisor.recommendation` arrives here as a frame. `format_recommendation` is a
pure Rich renderer (mirrors panels.format_memory) so it unit-tests directly.
The handler reveals the reserved `#advisor-dock` (hidden until the first
recommendation — the resize-hint toggle pattern) and renders the 5-part card
plainly (spec §6.C — polish + interaction affordances deferred to later tasks).
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Static

from redrobin.tui.textual_ui.frames import Registry

# Mirror the $verdict-*/sev tokens in theme.tcss (Rich styles take concrete hexes).
_RISK_COLORS = {"INFO": "#3fb950", "WARN": "#e3b341", "DANGER": "#f85149", "ESCALATE": "#58a6ff"}
_ACCENT = "#58a6ff"
_MUTED = "#8b949e"


def format_recommendation(payload: dict[str, Any]) -> Text:
    """Render the 5-part advisor card as Rich Text (plain — polish deferred)."""
    action = str(payload.get("suggested_action") or "")
    rationale = str(payload.get("rationale") or "")
    risk = str(payload.get("opsec_risk") or "INFO")
    confidence = str(payload.get("confidence") or "")
    references = payload.get("references") or []
    t = Text()
    t.append("ADVISOR", style=f"bold {_ACCENT}")
    t.append("  (advisory only — nothing dispatched)\n", style=_MUTED)
    t.append("→ ", style="bold")
    t.append(f"{action}\n", style="bold")
    t.append("why    ", style=_MUTED)
    t.append(f"{rationale}\n")
    t.append("opsec  ", style=_MUTED)
    t.append(f"{risk}\n", style=f"bold {_RISK_COLORS.get(risk, 'white')}")
    t.append("conf   ", style=_MUTED)
    t.append(f"{confidence}\n")
    t.append("refs   ", style=_MUTED)
    t.append(", ".join(str(r) for r in references) if references else "(none)")
    return t


def _on_recommendation(app: Any, payload: dict[str, Any]) -> None:
    """Reveal the docked advisor pane and render the latest recommendation."""
    try:
        dock = app.query_one("#advisor-dock")
    except NoMatches:
        return
    dock.display = True  # inline style overrides theme.tcss `display: none`
    text = format_recommendation(payload)
    try:
        card = app.query_one("#advisor-card", Static)
        card.update(text)
    except NoMatches:
        dock.mount(Static(text, id="advisor-card"))


def register_advisor(registry: Registry) -> None:
    """Bind the advisor.recommendation handler onto the shared registry. Called
    once from app.py after REGISTRY is defined (keeps app.py free of a circular
    import back into this module)."""
    registry.register("advisor.recommendation")(_on_recommendation)


__all__ = ["format_recommendation", "register_advisor"]
