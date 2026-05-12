"""redrobin/infrastructure/transport/bearer.py — bearer token lifecycle.

Generates/loads the operator's bearer token from
``REDROBIN_TRANSPORT_TOKEN_FILE`` (defaults to ``<REDROBIN_ROOT>/transport.token``
per :class:`Settings`). Adapters POST events with ``Authorization: Bearer
<token>``; the daemon validates.

The token is stored chmod 600 (POSIX) — see filesystem.py:secure_file().

Audit pass 2 #C2: this module used to capture ``REDROBIN_ROOT`` at import time
via ``from redrobin.infrastructure.filesystem import REDROBIN_ROOT``, breaking
test isolation. ``_token_path()`` now reads :class:`Settings` on every call so
``REDROBIN_ROOT`` (or the explicit override ``REDROBIN_TRANSPORT_TOKEN_FILE``)
takes effect even when set after this module's import.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from redrobin.infrastructure.filesystem import secure_file


def _token_path() -> Path:
    from redrobin.infrastructure.config import Settings

    path = Settings().transport_token_file
    assert path is not None  # Settings._derive_paths guarantees this
    return path


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
