"""redrobin/tui/panels/chat.py — Right pane: operator chat."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "operator" or "red-robin"
    text: str
    citations: list[str] | None = None


def render_chat(turns: list[ChatTurn]) -> str:
    """Render conversation as plain text."""
    if not turns:
        return "(chat empty — type to ask Red Robin)"
    lines: list[str] = []
    for turn in turns:
        prefix = "> " if turn.role == "operator" else "🐦 "
        lines.append(f"{prefix}{turn.text}")
        if turn.citations:
            for citation in turn.citations:
                lines.append(f"   ↪ {citation}")
        lines.append("")
    return "\n".join(lines).rstrip()
