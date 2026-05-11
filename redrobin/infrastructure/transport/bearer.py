"""redrobin/infrastructure/transport/bearer.py — bearer token lifecycle.

Generates/loads the operator's bearer token from `~/.redrobin/transport.token`.
Adapters POST events with `Authorization: Bearer <token>`; the daemon validates.

The token is stored chmod 600 (POSIX) — see filesystem.py:secure_file().
"""

from __future__ import annotations

import secrets
from pathlib import Path

from redrobin.infrastructure.filesystem import REDROBIN_ROOT, secure_file


def _token_path() -> Path:
    return REDROBIN_ROOT / "transport.token"


def get_or_create_token() -> str:
    """Load existing token from disk; generate + persist if absent. Idempotent."""
    path = _token_path()
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    secure_file(path, 0o600)
    return token
