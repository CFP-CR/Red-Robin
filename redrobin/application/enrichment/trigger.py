"""Enrich-by-default trigger: enrich unless output is too short or the command's
verb is on the operator-supplied denylist (empty by default — see config).

Fails TOWARD enrichment — an unknown/novel command is enriched by default, so a
new tool is never silently missed (the failure mode an allowlist would have).
Handles both bare commands (``ls``) and shell-wrapped ones (``shell ls``).
"""

from __future__ import annotations

# Beacon verbs that wrap an inner command (e.g. "shell ls"). A stable property of
# the C2/beacon grammar, not operator policy — intentionally not configurable.
_SHELL_WRAPPERS = ("shell", "run", "powershell", "pwsh", "execute")


def should_enrich(command: str, output: str, *, denylist: list[str], min_output_chars: int) -> bool:
    if len(output.strip()) < min_output_chars:
        return False
    parts = command.strip().split(maxsplit=1)
    verb = parts[0].lower() if parts else ""
    inner = ""
    if verb in _SHELL_WRAPPERS and len(parts) > 1:
        inner_parts = parts[1].strip().split(maxsplit=1)
        inner = inner_parts[0].lower() if inner_parts else ""
    deny = {d.lower() for d in denylist}
    if verb in deny:
        return False
    if inner and inner in deny:
        return False
    return True
