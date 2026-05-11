"""redrobin/application/event_ingestion.py — application-layer facade for event ingestion.

Re-exports infrastructure symbols (schema validators + REDROBIN_ROOT) so the api
layer can use them without violating the importlinter contract
``no-direct-infra-from-presentation``.
"""

from __future__ import annotations

from redrobin.infrastructure.filesystem import REDROBIN_ROOT
from redrobin.infrastructure.schema_validator import validate_envelope, validate_event

__all__ = ["REDROBIN_ROOT", "validate_envelope", "validate_event"]
