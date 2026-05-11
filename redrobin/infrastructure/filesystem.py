"""redrobin/infrastructure/filesystem.py — ~/.redrobin/ layout enforcement.

Per spec §5.1 storage layout and §5.7 file permissions. Idempotent.

The Red Robin operator's local state lives under ~/.redrobin/:
    shared/             — cross-engagement state (longterm.db, tradecraft.db, kb-index/)
    engagements/        — per-engagement isolated state (working_memory.json, episodic.db, events.jsonl)
    kb-source/          — operator-curated KB markdown
    rules/{shared,personal}/  — YAML rules
    config.toml, transport.token, redrobin.log

This module enforces:
    1. All directories exist (idempotent create).
    2. chmod 700 on directories (POSIX only; Windows permission model differs).
    3. secure_file() helper for chmod 600 on sensitive files (tokens, DBs, logs).
"""

from __future__ import annotations

import os
from pathlib import Path

REDROBIN_ROOT = Path.home() / ".redrobin"

# (relative_path, chmod_mode) — all POSIX-style permissions.
LAYOUT: tuple[tuple[str, int], ...] = (
    ("shared", 0o700),
    ("engagements", 0o700),
    ("engagements/archived", 0o700),
    ("kb-source", 0o700),
    ("rules", 0o700),
    ("rules/shared", 0o700),
    ("rules/personal", 0o700),
)


def ensure_layout() -> None:
    """Create ~/.redrobin/ tree with correct permissions. Idempotent."""
    REDROBIN_ROOT.mkdir(mode=0o700, exist_ok=True, parents=True)
    if os.name != "nt":
        REDROBIN_ROOT.chmod(0o700)
    for subpath, mode in LAYOUT:
        full = REDROBIN_ROOT / subpath
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
