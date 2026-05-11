"""redrobin/application/longterm_lifecycle.py — facade for L1/L3 repos at daemon boot.

Re-exports the infrastructure ``EpisodicRepo`` + ``LongtermRepo`` so the api
layer can wire them in lifespan without violating
``no-direct-infra-from-presentation``. Mirrors ``working_memory_lifecycle``.
"""

from __future__ import annotations

from redrobin.infrastructure.persistence.episodic_repo import EpisodicRepo
from redrobin.infrastructure.persistence.longterm_repo import LongtermRepo

__all__ = ["EpisodicRepo", "LongtermRepo"]
