"""PID file helper (cross-platform; uses os.kill(pid, 0) for liveness)."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid))


def read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            try:
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) == 0:
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        if e.errno == errno.EPERM:
            return True
        return False


class PidFile:
    """Context manager that writes the current PID and cleans up on exit.

    Raises ``RuntimeError`` on enter if the PID file already references a live
    process — this is the single-running-daemon guard.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "PidFile":
        existing = read_pid(self.path)
        if existing and is_pid_alive(existing):
            raise RuntimeError(f"daemon already running at pid {existing}")
        write_pid(self.path, os.getpid())
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
