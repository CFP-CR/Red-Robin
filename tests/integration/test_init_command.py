"""Slice 5: redrobin init."""
from __future__ import annotations

import io
import sys

import pytest

from redrobin.cli_helpers.init_cmd import run_init


def test_init_creates_structure(tmp_path):
    rc = run_init(
        root=tmp_path / "rr",
        anthropic_key="k1",
        voyage_key="k2",
        engagement_slug="op-first",
        interactive=False,
    )
    assert rc == 0
    assert (tmp_path / "rr" / "rules" / "shared").is_dir()
    assert (tmp_path / "rr" / "engagements" / "op-first" / "engagement.yaml").is_file()
    assert (tmp_path / "rr" / "adapters").is_dir()
    assert (tmp_path / "rr" / ".env").is_file()
    env = (tmp_path / "rr" / ".env").read_text()
    assert "ANTHROPIC_API_KEY=k1" in env
    assert "VOYAGE_API_KEY=k2" in env
    assert "REDROBIN_ENGAGEMENT_SLUG=op-first" in env


def test_init_idempotent_does_not_overwrite_existing(tmp_path):
    """Re-running init must not clobber existing .env or engagement.yaml."""
    target = tmp_path / "rr"
    run_init(root=target, anthropic_key="orig", voyage_key="orig", engagement_slug="op-a", interactive=False)
    rc = run_init(root=target, anthropic_key="new", voyage_key="new", engagement_slug="op-b", interactive=False)
    assert rc == 0
    env = (target / ".env").read_text()
    assert "ANTHROPIC_API_KEY=orig" in env, "init must not overwrite existing .env"


def test_init_sets_strict_perms_on_dotenv(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX perms")
    target = tmp_path / "rr"
    run_init(root=target, anthropic_key="k", voyage_key="k", engagement_slug="op", interactive=False)
    mode = (target / ".env").stat().st_mode & 0o777
    assert mode == 0o600


def test_init_creates_sample_rule(tmp_path):
    target = tmp_path / "rr"
    run_init(root=target, anthropic_key="k", voyage_key="k", engagement_slug="op", interactive=False)
    rules = list((target / "rules" / "shared").rglob("*.yaml"))
    assert len(rules) >= 1


def test_init_interactive_refuses_non_tty(tmp_path, monkeypatch, capsys):
    """Audit finding #7: getpass falls back to plain echo on non-TTY (Git Bash, CI).
    run_init(interactive=True) must refuse and return 2 with an ERROR pointing to
    --non-interactive."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # not a TTY
    target = tmp_path / "rr"
    rc = run_init(root=target, interactive=True)
    assert rc == 2
    out = capsys.readouterr()
    assert "TTY" in out.err
    assert "--non-interactive" in out.err
