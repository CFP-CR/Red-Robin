"""Slice 2: dotenv loads at CLI entry, NOT at import."""
from __future__ import annotations

import os
import subprocess
import sys


def test_dotenv_not_loaded_at_import(monkeypatch, tmp_path):
    """Importing redrobin.cli must not mutate os.environ."""
    monkeypatch.delenv("REDROBIN_TEST_DOTENV", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("REDROBIN_TEST_DOTENV=loaded\n")
    # subprocess so the import side-effects (if any) are isolated
    result = subprocess.run(
        [sys.executable, "-c",
         "import os; import redrobin.cli; print(os.environ.get('REDROBIN_TEST_DOTENV', 'NOT_LOADED'))"],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "NOT_LOADED" in result.stdout, "dotenv was loaded at import time — this breaks test isolation"


def test_dotenv_loaded_when_doctor_invoked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=from-dotenv\n"
        "VOYAGE_API_KEY=from-dotenv\n"
        "REDROBIN_ROOT=" + str(tmp_path) + "\n"
    )
    (tmp_path / "rules" / "shared").mkdir(parents=True)
    # Pre-clear env to ensure dotenv is what populates them
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.cli import cmd_doctor
    # Call positionally — cmd_doctor's parameter is named `_args` (unused),
    # so a keyword would mismatch. The intent here is verifying dotenv-loaded
    # secrets reach Settings, not the parameter name.
    rc = cmd_doctor(type("A", (), {"verbose": False})())
    assert rc == 0


def test_explicit_env_wins_over_dotenv(monkeypatch, tmp_path):
    """Explicit os.environ value MUST win over .env value (safe precedence)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("REDROBIN_PORT=9999\n")
    monkeypatch.setenv("REDROBIN_PORT", "7777")

    from redrobin.cli import _load_env_for_cli
    _load_env_for_cli()
    assert os.environ["REDROBIN_PORT"] == "7777"


def test_dotenv_does_NOT_walk_parent_dirs(monkeypatch, tmp_path):
    """Audit finding #4: pinning load_dotenv to Path.cwd() / '.env' prevents
    accidental pickup of a parent-directory .env (cross-engagement secret leak)."""
    # Layout: tmp_path/parent/.env (DANGEROUS) and tmp_path/parent/cwd/ (no .env)
    parent = tmp_path / "parent"
    cwd = parent / "cwd"
    cwd.mkdir(parents=True)
    (parent / ".env").write_text("REDROBIN_LEAKED_SECRET=parent_dir_value\n")
    monkeypatch.delenv("REDROBIN_LEAKED_SECRET", raising=False)
    monkeypatch.chdir(cwd)

    from redrobin.cli import _load_env_for_cli
    _load_env_for_cli()
    assert "REDROBIN_LEAKED_SECRET" not in os.environ, \
        "parent-directory .env must NOT be loaded — dotenv path must be pinned to CWD only"
