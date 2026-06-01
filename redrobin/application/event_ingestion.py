"""redrobin/application/event_ingestion.py — application-layer facade for event ingestion.

Re-exports infrastructure symbols (schema validators + ``redrobin_root``) so the api
layer can use them without violating the importlinter contract
``no-direct-infra-from-presentation``.

Audit pass 2 #C2: this facade used to re-export the ``REDROBIN_ROOT`` module-level
constant, which was resolved at import time and broke test isolation. The
re-export is now the ``redrobin_root()`` accessor function, which reads
:class:`Settings` lazily on every call.
"""

from __future__ import annotations

from redrobin.infrastructure.filesystem import redrobin_root
from redrobin.infrastructure.schema_validator import validate_envelope, validate_event

__all__ = ["redrobin_root", "validate_envelope", "validate_event"]
