"""Slice 2: dotenv loads at CLI entry, NOT at import."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


# This module exists to call ``_load_env_for_cli``, so it is the single biggest
# producer of the permanent ``os.environ`` writes tracked by issue #51 — including
# the fake keys that used to be the ONLY thing supplying credentials to every
# later daemon-booting test in CI. Restore after each test; the modules that were
# living off that leak now pin REDROBIN_ALLOW_DEGRADED themselves.
pytestmark = pytest.mark.usefixtures("restore_environ")


def test_dotenv_not_loaded_at_import(monkeypatch, tmp_path):
    """Importing redrobin.cli must not mutate os.environ."""
    monkeypatch.delenv("REDROBIN_TEST_DOTENV", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("REDROBIN_TEST_DOTENV=loaded\n")
    # subprocess so the import side-effects (if any) are isolated
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; import redrobin.cli; print(os.environ.get('REDROBIN_TEST_DOTENV', 'NOT_LOADED'))",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "NOT_LOADED" in result.stdout, (
        "dotenv was loaded at import time — this breaks test isolation"
    )


def test_dotenv_loaded_when_doctor_invoked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=from-dotenv\n"
        "VOYAGE_API_KEY=from-dotenv\n"
        "REDROBIN_ROOT=" + str(tmp_path) + "\n"
    )
    # Pre-clear env to ensure dotenv is what populates them
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from redrobin.cli import cmd_doctor

    # Call positionally — the intent here is verifying dotenv-loaded
    # secrets reach Settings, not real network connectivity. Use quick=True
    # so Slice 6's network probes don't try to dial Anthropic/Voyage with
    # the fake `from-dotenv` value.
    rc = cmd_doctor(type("A", (), {"verbose": False, "quick": True})())
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
    assert "REDROBIN_LEAKED_SECRET" not in os.environ, (
        "parent-directory .env must NOT be loaded — dotenv path must be pinned to CWD only"
    )


# --- the root .env that `redrobin init` writes ------------------------------
#
# `redrobin init` writes its .env into the ROOT it creates, so an operator who
# runs the documented flow from the checkout got MISSING keys and no engagement
# out of every later command: the CLI only ever read `cwd/.env`. The CLI now
# loads BOTH, in the order that keeps precedence
# `shell env > cwd .env > root .env`.


def test_root_dotenv_is_loaded_when_root_comes_from_the_shell(monkeypatch, tmp_path):
    """`<root>/.env` is loaded even when the CWD has no .env at all."""
    root = tmp_path / "rr"
    root.mkdir()
    (root / ".env").write_text("REDROBIN_ROOT_ONLY_KEY=from-root\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    monkeypatch.delenv("REDROBIN_ROOT_ONLY_KEY", raising=False)

    from redrobin.cli import _load_env_for_cli

    _load_env_for_cli()
    assert os.environ.get("REDROBIN_ROOT_ONLY_KEY") == "from-root", (
        "the CLI must load the .env from the root it operates on, not only from the CWD"
    )


def test_root_dotenv_is_found_via_root_declared_in_the_cwd_dotenv(monkeypatch, tmp_path):
    """Chicken-and-egg: the root itself may be declared by the CWD .env.

    So the root .env can only be resolved AFTER the CWD load.
    """
    root = tmp_path / "rr"
    root.mkdir()
    (root / ".env").write_text("REDROBIN_ROOT_ONLY_KEY=from-root\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text(f"REDROBIN_ROOT={root}\n")
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("REDROBIN_ROOT", raising=False)
    monkeypatch.delenv("REDROBIN_ROOT_ONLY_KEY", raising=False)

    from redrobin.cli import _load_env_for_cli

    _load_env_for_cli()
    assert os.environ.get("REDROBIN_ROOT_ONLY_KEY") == "from-root"


def test_root_dotenv_falls_back_to_the_default_root(monkeypatch, tmp_path):
    """With no REDROBIN_ROOT anywhere, the root is the same default the rest of
    the code uses (``Settings.root`` -> ``~/.redrobin``) — the exact path a bare
    `redrobin init` writes to."""
    home = tmp_path / "home"
    (home / ".redrobin").mkdir(parents=True)
    (home / ".redrobin" / ".env").write_text("REDROBIN_ROOT_ONLY_KEY=from-default-root\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("REDROBIN_ROOT", raising=False)
    monkeypatch.delenv("REDROBIN_ROOT_ONLY_KEY", raising=False)
    # Path.home() reads these (posixpath/ntpath expanduser); set both so the
    # default-root branch resolves under tmp_path on every platform.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    from redrobin.cli import _load_env_for_cli

    _load_env_for_cli()
    assert os.environ.get("REDROBIN_ROOT_ONLY_KEY") == "from-default-root"


def test_precedence_shell_beats_cwd_dotenv_beats_root_dotenv(monkeypatch, tmp_path):
    """Both .env files load, but neither may clobber anything already set."""
    root = tmp_path / "rr"
    root.mkdir()
    (root / ".env").write_text(
        "REDROBIN_PREC_SHELL=root\nREDROBIN_PREC_CWD=root\nREDROBIN_PREC_ROOT=root\n"
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text(
        f"REDROBIN_ROOT={root}\nREDROBIN_PREC_SHELL=cwd\nREDROBIN_PREC_CWD=cwd\n"
    )
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("REDROBIN_PREC_SHELL", "shell")
    monkeypatch.delenv("REDROBIN_PREC_CWD", raising=False)
    monkeypatch.delenv("REDROBIN_PREC_ROOT", raising=False)

    from redrobin.cli import _load_env_for_cli

    _load_env_for_cli()
    assert os.environ["REDROBIN_PREC_SHELL"] == "shell"
    assert os.environ["REDROBIN_PREC_CWD"] == "cwd"
    assert os.environ["REDROBIN_PREC_ROOT"] == "root"
