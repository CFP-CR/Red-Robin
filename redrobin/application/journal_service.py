"""redrobin/application/journal_service.py — the Operational Journal (6th memory
layer, engagement axis).

The verbatim (command, output) transcript of the operation, so the assistant can
re-read the exact text of any past moment (the other layers are distilled/lossy).
Mirrors KBService (application/kb_service.py) — same VoyageClient + VectorStore
infra, same retrieve() shape — but its source is the engagement's events.jsonl,
NOT curated markdown, and it stays current via an incremental refresh. The raw
material already exists as TWO separate events (beacon.command_input +
beacon.command_output) correlated by correlation_id (spec §6.B), so this module
joins them; it captures no new data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JournalEntry:
    """One verbatim moment: the command the operator ran and the output it produced,
    joined from the two events.jsonl events sharing this correlation_id."""

    correlation_id: str
    command: str
    output: str


def join_transcript(
    event_bodies: Iterable[dict[str, Any]], commands: dict[str, str] | None = None
) -> list[JournalEntry]:
    """Join beacon.command_input + beacon.command_output by correlation_id into
    verbatim (command, output) entries (spec §6.B). Events without a correlation_id
    are skipped (the offline index keys on the stable id; the enricher's per-bid
    fallback is a live-stream recovery, not applicable here). An output with no
    seen input yields an empty command (still a verbatim record).

    `commands` is an optional caller-owned correlation_id -> command map. When the
    same dict is passed across successive calls (the incremental refresh), an output
    pairs with an input read in an earlier call. Omit it for a one-shot join."""
    commands = {} if commands is None else commands
    entries: list[JournalEntry] = []
    for body in event_bodies:
        corr = body.get("correlation_id")
        if corr is None:
            continue
        corr = str(corr)
        etype = body.get("type")
        if etype == "beacon.command_input":
            commands[corr] = str(body.get("raw") or body.get("command") or "")
        elif etype == "beacon.command_output":
            entries.append(
                JournalEntry(
                    correlation_id=corr,
                    command=commands.get(corr, ""),
                    output=str(body.get("output") or ""),
                )
            )
    return entries
