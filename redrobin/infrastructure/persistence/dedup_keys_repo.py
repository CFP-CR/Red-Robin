"""redrobin/infrastructure/persistence/dedup_keys_repo.py — JSON-backed set
of dedup keys, persistent per engagement.

The dedup gate in ``events_router.ingest`` uses this set to drop duplicate
re-emissions of producer events (the `.cna` re-fires Aggressor hooks on every
CS-client reconnect, multiplying ingest by hundreds — see spec
``docs/superpowers/specs/2026-05-28-cna-event-dedup-design.md``).

Atomic write contract: serialize to a sibling ``.tmp`` then ``os.replace()`` it
onto the target. A concurrent reader therefore sees either the old or the new
complete file — never the empty window a plain truncate-then-write exposes.
``os.replace`` is atomic on POSIX; on Windows it raises ``PermissionError``
while a reader holds the target open, so we retry briefly before giving up.
Pattern mirrors ``WorkingMemoryRepo.save`` (working_memory_repo.py).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class DedupKeysRepo:
    """Read/write a single-engagement JSON file holding a set of dedup keys.

    Format on disk: ``{"keys": ["<key>", ...]}``. The in-memory representation
    is ``set[str]``; keys are opaque strings produced by the application layer
    (``redrobin.application.dedup_keys.derive_key``).
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("dedup_keys: corrupt file at %s; loading empty", self.path)
            return set()
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            logger.error("dedup_keys: wrong shape in %s; loading empty", self.path)
            return set()
        return {str(k) for k in data["keys"]}

    def save(self, keys: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps({"keys": sorted(keys)}, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 9:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    def clear(self) -> None:
        """Delete the underlying file; missing-file is a no-op."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
