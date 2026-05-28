"""redrobin/application/dedup_keys.py — application-layer dedup state.

``DedupKeys`` holds an in-memory ``set[str]`` and an ``asyncio.Lock``,
delegates persistence to ``DedupKeysRepo``, and exposes a single async
``check_and_insert(key)`` that the ingest path calls before applying side
effects (append-to-jsonl, bus publish).

``derive_key(event)`` is a pure function that maps a wire event payload to
a stable dedup key string, or returns ``None`` for events that should
pass-through unfiltered (events without ``correlation_id`` other than
``beacon.initial``).

Pattern: Idempotent Receiver (Hohpe & Woolf). Producer-attached key
(``correlation_id`` from the `.cna`'s Aggressor ``$5``), composite with
``beacon_id`` + ``event_type`` to defend against cross-event-type collisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redrobin.infrastructure.persistence.dedup_keys_repo import DedupKeysRepo

logger = logging.getLogger(__name__)

# Event types that carry a correlation_id and that we dedup with the
# (bid, correlation_id, type) composite key.
_CORRELATED_TYPES = frozenset({"beacon.command_input", "beacon.command_output", "opsec.verdict"})


def derive_key(event: dict[str, Any]) -> str | None:
    """Return the dedup key for a wire event payload, or ``None`` for
    pass-through (event types we choose not to dedup).

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events,
    ``"<bid>||<event_type>"`` for ``beacon.initial`` (no correlation_id; exactly
    one initial per beacon by definition).
    """
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    # opsec.verdict uses `beacon_id`; the beacon.* events use `bid`.
    bid_raw = event.get("bid") or event.get("beacon_id")
    if not bid_raw:
        return None
    bid = str(bid_raw)

    if event_type == "beacon.initial":
        return f"{bid}||{event_type}"

    if event_type in _CORRELATED_TYPES:
        corr_raw = event.get("correlation_id")
        if not corr_raw:
            return None
        return f"{bid}|{corr_raw}|{event_type}"

    return None


class DedupKeys:
    """In-memory set of seen dedup keys, persisted via ``DedupKeysRepo``.

    Use :meth:`load` to hydrate from disk at lifespan boot. ``check_and_insert``
    is the single async entry point: it returns ``True`` if the key was new
    (and the state was persisted) or ``False`` if the key was already present.
    """

    def __init__(self, repo: DedupKeysRepo, keys: set[str]) -> None:
        self._repo = repo
        self._keys = keys
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, repo: DedupKeysRepo) -> "DedupKeys":
        return cls(repo=repo, keys=repo.load())

    async def check_and_insert(self, key: str) -> bool:
        """Atomically check + insert + persist. Returns True iff key is new.

        Save-first-then-mutate: persist the candidate set BEFORE mutating the
        in-memory set, so a ``save()`` failure leaves disk and memory both
        unchanged (no drift where memory has the key but disk doesn't, which
        would silently un-dedup the next process restart).
        """
        async with self._lock:
            if key in self._keys:
                return False
            new_keys = self._keys | {key}
            self._repo.save(new_keys)  # may raise — both stores unchanged on failure
            self._keys = new_keys
            return True

    def clear(self) -> None:
        """Drop the in-memory set and the on-disk file."""
        self._keys.clear()
        self._repo.clear()
