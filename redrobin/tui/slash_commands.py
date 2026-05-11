"""redrobin/tui/slash_commands.py — `:rr <verb> [args]` parser.

Spec §4 Componente 5 canonical set:
  :rr alts | :rr explain | :rr skip | :rr override "<reason>"
  :rr exec <cmd> | :rr promote "<lesson>" | :rr milestone "<text>"
  :rr phase <name> | :rr objective <id> <pct> | :rr inspector | :rr help
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

VALID_VERBS = {
    "alts",
    "explain",
    "skip",
    "override",
    "exec",
    "promote",
    "milestone",
    "phase",
    "objective",
    "inspector",
    "help",
}


@dataclass(frozen=True)
class SlashCommand:
    verb: str
    args: list[str]


def parse(text: str) -> SlashCommand | None:
    """Parse ':rr <verb> [args]'. Returns None if not a slash command."""
    text = text.strip()
    if not text.startswith(":rr"):
        return None
    body = text[3:].strip()
    if not body:
        return SlashCommand(verb="help", args=[])
    try:
        tokens = shlex.split(body)
    except ValueError:
        # Unbalanced quotes — return None to signal parse error
        return None
    if not tokens:
        return SlashCommand(verb="help", args=[])
    verb = tokens[0].lower()
    if verb not in VALID_VERBS:
        return SlashCommand(verb="help", args=[verb, *tokens[1:]])
    return SlashCommand(verb=verb, args=tokens[1:])
