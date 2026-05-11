"""redrobin/tui/slash_commands.py — Slash command parser.

Two surface forms parse to the same :class:`SlashCommand`:

* Canonical, scriptable: ``:rr <verb> [args]`` — works in chat-style input.
* TUI shorthand: ``/<verb> [args]`` — what the ``/`` keybinding seeds and
  what the operator types interactively. A bare ``/`` opens help.

Spec §4 Componente 5 canonical set:
  :rr alts | :rr explain | :rr skip | :rr override "<reason>"
  :rr exec <cmd> | :rr promote "<lesson>" | :rr milestone "<text>"
  :rr phase <name> | :rr objective <id> <pct> | :rr inspector | :rr help

The parser returns ``None`` if the text is neither a ``:rr`` nor a ``/``
form, signalling to the caller (``redrobin/tui/app.py:on_submit``) that
the input should be dispatched to the daemon's verdict endpoint instead.
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
    """Parse ``:rr <verb> [args]`` or ``/<verb> [args]``.

    Returns ``None`` for input that is neither prefix; the caller treats
    that as plain dispatcher input.
    """
    text = text.strip()
    if text.startswith(":rr"):
        body = text[3:].strip()
    elif text.startswith("/"):
        body = text[1:].strip()
    else:
        return None

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
