"""redrobin/infrastructure/filesystem.py — ~/.redrobin/ layout enforcement.

Per spec §5.1 storage layout and §5.7 file permissions. Idempotent.

The Red Robin operator's local state lives under ~/.redrobin/ (or whatever
``REDROBIN_ROOT`` resolves to via :class:`Settings`):
    shared/             — cross-engagement state (longterm.db, tradecraft.db, kb-index/)
    engagements/        — per-engagement isolated state (working_memory.json, episodic.db, events.jsonl)
    kb-source/          — operator-curated KB markdown
    config.toml, transport.token, redrobin.log

This module enforces:
    1. All directories exist (idempotent create).
    2. chmod 700 on directories (POSIX only; Windows permission model differs).
    3. secure_file() helper for chmod 600 on sensitive files (tokens, DBs, logs).

Audit pass 2 #C2: the root path used to be a module-level ``REDROBIN_ROOT``
constant resolved at import time, which broke test isolation when a test set
``REDROBIN_ROOT`` via ``monkeypatch.setenv`` after this module was already
imported. ``redrobin_root()`` reads :class:`Settings` lazily on every call so
the env var always wins; ``Settings`` is imported inside the function to keep
this module's import side-effect-free at module load.
"""

from __future__ import annotations

import os
from pathlib import Path

# (relative_path, chmod_mode) — all POSIX-style permissions.
LAYOUT: tuple[tuple[str, int], ...] = (
    ("shared", 0o700),
    ("engagements", 0o700),
    ("engagements/archived", 0o700),
    ("kb-source", 0o700),
)


def redrobin_root() -> Path:
    """Return the operator's local root path. Reads :class:`Settings` lazily.

    Imported inside the function so the infrastructure layer doesn't pull
    :mod:`redrobin.infrastructure.config` at import time (keeps this module's
    import graph minimal and lets tests mutate ``REDROBIN_ROOT`` via env var
    without needing to re-import).
    """
    from redrobin.infrastructure.config import Settings

    return Settings().root


def ensure_layout() -> None:
    """Create ~/.redrobin/ tree with correct permissions. Idempotent."""
    root = redrobin_root()
    root.mkdir(mode=0o700, exist_ok=True, parents=True)
    if os.name != "nt":
        root.chmod(0o700)
    for subpath, mode in LAYOUT:
        full = root / subpath
        full.mkdir(mode=mode, exist_ok=True, parents=True)
        if os.name != "nt":
            full.chmod(mode)


def secure_file(path: Path, mode: int = 0o600) -> None:
    """Set restrictive permissions on a sensitive file (token, DB, log).

    No-op on Windows (different permission model — operator should use disk
    encryption per spec §5.8).
    """
    if os.name != "nt":
        path.chmod(mode)
