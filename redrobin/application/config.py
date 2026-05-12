"""redrobin/application/config.py — typed Settings facade.

Thin application-layer pass-through to the infrastructure config module, so
the api layer never imports infrastructure directly (importlinter contract
``no-direct-infra-from-presentation``).

Slice 3 (v0.1.x hardening) extends the facade to also re-export
:func:`validate_for_boot` — the FastAPI lifespan needs to call it after
constructing ``Settings()`` but cannot reach infrastructure directly under
the import-linter contract.
"""

from __future__ import annotations

from redrobin.infrastructure.boot_validation import validate_for_boot
from redrobin.infrastructure.config import Settings

__all__ = ["Settings", "validate_for_boot"]
