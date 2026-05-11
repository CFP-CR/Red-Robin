"""Slice 11 integration: daemon stop, kb add, replay."""
from __future__ import annotations

import os

from redrobin.cli_helpers.daemon_pidfile import (
    PidFile,
    is_pid_alive,
    read_pid,
    write_pid,
)


def test_pidfile_roundtrip(tmp_path):
    p = tmp_path / "rr.pid"
    write_pid(p, 12345)
    assert read_pid(p) == 12345
    p.unlink()


def test_is_pid_alive_for_current_process():
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_for_dead_pid():
    assert is_pid_alive(0) is False


def test_pidfile_context(tmp_path):
    p = tmp_path / "rr.pid"
    with PidFile(p) as pf:
        assert read_pid(p) == os.getpid()
        assert pf.path == p
    assert not p.exists()
