"""redrobin/application/working_memory_lifecycle.py — application facade for working memory persistence.

Re-exports the infrastructure ``WorkingMemoryRepo`` (plus the ``WorkingMemory``
domain type used to seed a fresh file) so the api layer can wire the reducer at
daemon boot without violating the importlinter contract
``no-direct-infra-from-presentation``. Mirrors ``rules_lifecycle.py``.
"""

from __future__ import annotations

from redrobin.domain.memory import WorkingMemory
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

__all__ = ["WorkingMemory", "WorkingMemoryRepo"]
