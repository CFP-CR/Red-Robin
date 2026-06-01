"""redrobin/application/dedup_keys.py — application-layer dedup state.

``DedupKeys`` holds an in-memory ``set[str]`` and an ``asyncio.Lock``,
delegates persistence to ``DedupKeysRepo``, and exposes a single async
``check_and_insert(key)`` that the ingest path calls before applying side
effects (append-to-jsonl, bus publish).

``derive_key(event)`` is a pure function that maps a wire event payload to
a stable dedup key string, or returns ``None`` for events we don't dedup:
events without a ``correlation_id``, AND ``beacon.initial`` — which is
idempotent at the reducer (``upsert_session`` replaces by ``bid``), so
deduping it would only break session re-seeding after a working-memory
clear (Group D).

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

    Key shape: ``"<bid>|<correlation_id>|<event_type>"`` for command events
    (``beacon.command_input``/``beacon.command_output``/``opsec.verdict``).
    ``beacon.initial`` is pass-through (``None``) — idempotent at the reducer,
    so deduping it would break the ``.cna`` backfill's session re-seed.
    """
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    # opsec.verdict uses `beacon_id`; the beacon.* events use `bid`.
    bid_raw = event.get("bid") or event.get("beacon_id")
    if not bid_raw:
        return None
    bid = str(bid_raw)

    # beacon.initial is intentionally NOT deduped (Group D): it is idempotent at
    # the reducer (upsert_session replaces by bid), so deduping it gains nothing
    # and would drop the .cna backfill's re-emit that re-seeds sessions after a
    # working-memory clean. It falls through to None below = pass-through.

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
        if not isinstance(key, str):
            raise TypeError(f"DedupKeys.check_and_insert expects str, got {type(key).__name__}")
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

    def __len__(self) -> int:
        """Number of dedup keys currently held in memory.

        Cheap O(1) accessor so callers (lifespan boot log, observability
        endpoints) don't have to re-read the file via ``repo.load()`` just
        to know the size of the already-loaded set.
        """
        return len(self._keys)
