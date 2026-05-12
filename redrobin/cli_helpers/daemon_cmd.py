"""``redrobin daemon {start,stop}`` business logic — extracted in Slice 12.

The CLI wrappers in :mod:`redrobin.cli` are thin shells that load ``.env`` and
construct ``Settings()``; :func:`run_daemon_start` / :func:`run_daemon_stop`
here do the real work so the bodies are testable without going through
argparse.
"""
from __future__ import annotations

import os
import sys
import time

from redrobin.application.transport_auth import get_or_create_token
from redrobin.cli_helpers.daemon_pidfile import PidFile, is_pid_alive, read_pid
from redrobin.infrastructure.boot_validation import validate_for_boot
from redrobin.infrastructure.config import Settings
from redrobin.infrastructure.filesystem import ensure_layout


def run_daemon_start(
    *,
    host: str | None,
    port: int | None,
    allow_degraded: bool,
) -> int:
    """Launch the daemon (uvicorn) under a PID-file lock.

    Slice 3 (v0.1.x hardening): ``allow_degraded=True`` exports
    ``REDROBIN_ALLOW_DEGRADED=1`` BEFORE :class:`Settings` reads it, equivalent
    to setting the env var at the shell but discoverable via ``--help``.

    Validates the boot environment BEFORE binding the port so missing secrets
    crash the CLI with a clear error instead of an apparently-running daemon
    that swallows the silent ``_DegradedLayerB`` fallback.
    """
    if allow_degraded:
        os.environ["REDROBIN_ALLOW_DEGRADED"] = "1"
    import uvicorn

    settings = Settings()
    validate_for_boot(settings, require_root_exists=True)
    pid_path = settings.pid_file
    assert pid_path is not None  # Settings._derive_paths guarantees this
    ensure_layout()
    get_or_create_token()
    resolved_host = host or "127.0.0.1"
    resolved_port = port or 7777
    print(f"[*] Starting Red Robin daemon on {resolved_host}:{resolved_port}")
    with PidFile(pid_path):
        uvicorn.run(
            "redrobin.api.app:create_app",
            host=resolved_host,
            port=resolved_port,
            factory=True,
            log_level="info",
        )
    return 0


def run_daemon_stop() -> int:
    """Stop the running daemon by sending TERM (Unix) or TerminateProcess (Win)."""
    settings = Settings()
    pid_path = settings.pid_file
    assert pid_path is not None  # Settings._derive_paths guarantees this
    pid = read_pid(pid_path)
    if pid is None:
        print("no daemon running (no PID file)")
        return 0
    if not is_pid_alive(pid):
        print(f"PID file references dead process {pid}; removing")
        pid_path.unlink(missing_ok=True)
        return 0
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            print(f"failed to open process {pid}")
            return 1
        try:
            kernel32.TerminateProcess(handle, 0)
        finally:
            kernel32.CloseHandle(handle)
        deadline = time.time() + 10
        while is_pid_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    else:
        import signal

        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 10
        while is_pid_alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    pid_path.unlink(missing_ok=True)
    print(f"daemon stopped (pid={pid})")
    return 0
