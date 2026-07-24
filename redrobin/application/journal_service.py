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

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redrobin.infrastructure.llm.voyage_client import VoyageClient
from redrobin.infrastructure.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Per-entry output cap so one huge command output can't dominate the index or blow
# the agent's context (spec §6.B refinement 2/3: cap, high-signal excerpts not dumps).
_MAX_ENTRY_CHARS = 2000
# Embed in batches so a large backlog degrades to a partial index on a Voyage
# batch-limit error, never all-or-nothing (audit finding).
_EMBED_BATCH = 128


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


def _event_bodies(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Parse events.jsonl lines → event bodies. Each line is a wire envelope
    ({spec_version, id, ts, source, event:{type,...}}); lift the flat `event` body
    (which carries `type`, `correlation_id`, `raw`, `output`, ...). Tolerate a
    pre-flattened dict, blank lines, and malformed JSON (skip) so a partial or
    hand-crafted trace never crashes indexing (never-crash, spec §6.E)."""
    bodies: list[dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event = data.get("event")
        bodies.append(event if isinstance(event, dict) else data)
    return bodies


def _entry_text(entry: JournalEntry) -> str:
    """The verbatim moment as one embeddable/citable excerpt, output capped."""
    output = entry.output[:_MAX_ENTRY_CHARS]
    return f"$ {entry.command}\n{output}" if entry.command else output


class JournalService:
    """Index + retrieve the engagement's verbatim transcript, kept current by an
    incremental refresh that reads only the events.jsonl lines appended since the
    last index. Mirrors KBService's retrieve() shape."""

    def __init__(self, *, voyage: VoyageClient, store: VectorStore, events_path: Path) -> None:
        self._voyage = voyage
        self._store = store
        self._events_path = events_path
        self._consumed = 0  # number of COMPLETE lines already indexed
        self._commands: dict[str, str] = {}  # persistent corr->command across refreshes
        self._seq = 0  # monotonic, for unique store ids (multiple outputs may share a corr)
        self._lock = asyncio.Lock()  # serialize concurrent refreshes (no double-index)

    async def refresh(self) -> int:
        """Read the events.jsonl lines appended since the last refresh, join them
        (continuing the persistent corr->command map), embed the new entries in
        batches, and add them to the store. Returns entries added this call.

        Atomic + retryable: the store, the line offset, and the command map are
        committed ONLY after every batch embeds successfully. A trailing partial
        line (no newline yet) is left for the next refresh. Any failure — a
        missing/unreadable file or an embedding error — is logged and returns 0
        without advancing, so the same lines are retried next call; refresh never
        raises past a logged warning (never-crash, spec §6.E)."""
        async with self._lock:
            if not self._events_path.exists():
                return 0
            try:
                text = self._events_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                logger.exception("assistant: journal could not read %s", self._events_path)
                return 0
            # Split on newline; the last element is "" (file ended with \n) or a
            # still-being-written partial line — either way, don't consume it yet.
            complete = text.split("\n")[:-1]
            new_lines = complete[self._consumed :]
            # Join into a COPY of the persistent map so a mid-embed failure leaves
            # neither the store, the offset, nor the command map half-updated.
            pending_commands = dict(self._commands)
            entries = join_transcript(_event_bodies(new_lines), pending_commands)
            if not entries:
                # Nothing to embed (e.g. input-only lines) — commit the advanced
                # offset + remembered commands so we don't reprocess them.
                self._consumed = len(complete)
                self._commands = pending_commands
                return 0
            # Embed the delta in batches (Voyage request-size limits), staging the
            # results BEFORE mutating the store — so a failure on any batch is a
            # clean no-op that retries on the next refresh (never a lost/partial
            # index, never a raised exception past this logged warning).
            staged: list[tuple[list[str], list[str], list[list[float]], list[dict[str, Any]]]] = []
            try:
                for i in range(0, len(entries), _EMBED_BATCH):
                    batch = entries[i : i + _EMBED_BATCH]
                    texts = [_entry_text(e) for e in batch]
                    ids = [
                        f"journal:{e.correlation_id}#{self._seq + i + j}"
                        for j, e in enumerate(batch)
                    ]
                    metas: list[dict[str, Any]] = [
                        {"source": f"journal:{e.correlation_id}", "command": e.command}
                        for e in batch
                    ]
                    result = await self._voyage.embed_documents(texts)
                    staged.append((ids, texts, list(result.vectors), metas))
            except Exception:
                logger.exception("assistant: journal embedding failed — will retry on next refresh")
                return 0
            all_ids: list[str] = []
            all_texts: list[str] = []
            all_vectors: list[list[float]] = []
            all_metas: list[dict[str, Any]] = []
            for ids, texts, vectors, metas in staged:
                all_ids += ids
                all_texts += texts
                all_vectors += vectors
                all_metas += metas
            # ONE store mutation: a mid-add failure can't leave an earlier batch indexed with the
            # offset unadvanced (which would double-index it on the next refresh).
            self._store.add(ids=all_ids, texts=all_texts, vectors=all_vectors, metadatas=all_metas)
            self._consumed = len(complete)
            self._seq += len(entries)
            self._commands = pending_commands
            return len(entries)

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[tuple[str, str, float]]:
        """Catch the index up to the latest events.jsonl, then return the top-K
        matching moments. Each entry: (text, source, similarity)."""
        await self.refresh()
        if self._store.count() == 0:
            return []
        query_vec = await self._voyage.embed_query(query)
        hits = self._store.search(query_vector=query_vec, top_k=top_k)
        return [(rec.text, rec.metadata.get("source", ""), score) for rec, score in hits]


__all__ = ["JournalEntry", "JournalService", "join_transcript"]
