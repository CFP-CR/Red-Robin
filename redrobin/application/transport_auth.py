"""redrobin/application/transport_auth.py — transport bearer-token facade.

Thin application-layer pass-through to the infrastructure bearer module, so the
api layer never imports infrastructure directly (importlinter contract
``no-direct-infra-from-presentation``).
"""

from __future__ import annotations

from redrobin.infrastructure.transport.bearer import get_or_create_token

__all__ = ["get_or_create_token"]
