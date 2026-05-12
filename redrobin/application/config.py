"""redrobin/application/config.py — typed Settings facade.

Thin application-layer pass-through to the infrastructure config module, so
the api layer never imports infrastructure directly (importlinter contract
``no-direct-infra-from-presentation``).
"""

from __future__ import annotations

from redrobin.infrastructure.config import Settings

__all__ = ["Settings"]
