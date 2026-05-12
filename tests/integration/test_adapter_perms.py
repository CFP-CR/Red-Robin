"""Slice 4: adapter credential file permissions."""
from __future__ import annotations

import os
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms test")
def test_loose_perms_warns(tmp_path, capsys):
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    f = adapter_dir / "cs-main.yaml"
    f.write_text("name: cs-main\nkind: cobaltstrike\nteamserver_url: https://x\noperator_user: u\noperator_password: pw\nverify_ssl: false\n")
    os.chmod(f, 0o644)
    from redrobin.infrastructure.cs_adapter_loader import load_adapter_configs
    cfgs = load_adapter_configs(adapter_dir)
    out = capsys.readouterr()
    assert "loose permissions" in out.err or "loose permissions" in out.out
    assert len(cfgs) == 1  # still loads


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX perms test")
def test_strict_perms_no_warn(tmp_path, capsys):
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    f = adapter_dir / "cs-main.yaml"
    f.write_text("name: cs-main\nkind: cobaltstrike\nteamserver_url: https://x\noperator_user: u\noperator_password: pw\nverify_ssl: false\n")
    os.chmod(f, 0o600)
    from redrobin.infrastructure.cs_adapter_loader import load_adapter_configs
    load_adapter_configs(adapter_dir)
    out = capsys.readouterr()
    assert "loose permissions" not in out.err
    assert "loose permissions" not in out.out


def test_no_password_in_logs(tmp_path, caplog):
    """Loader must never log the password value."""
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    f = adapter_dir / "cs-main.yaml"
    f.write_text("name: cs-main\nkind: cobaltstrike\nteamserver_url: https://x\noperator_user: u\noperator_password: VERYSECRET\nverify_ssl: false\n")
    from redrobin.infrastructure.cs_adapter_loader import load_adapter_configs
    load_adapter_configs(adapter_dir)
    for record in caplog.records:
        assert "VERYSECRET" not in record.getMessage()
