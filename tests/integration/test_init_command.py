"""Slice 5: redrobin init."""

from __future__ import annotations

import io
import sys

import pytest
from dotenv import dotenv_values

from redrobin.application.engagement_service import EngagementService
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
    # No `rules/` — the OPSEC rule pack went with the pre-flight gate (2026-07-26).
    assert not (tmp_path / "rr" / "rules").exists()
    # No `adapters/` — it only ever held YAML for the outbound CS REST client,
    # which went with the dispatch removal. Capture needs no on-disk config.
    assert not (tmp_path / "rr" / "adapters").exists()
    assert (tmp_path / "rr" / "engagements" / "op-first" / "engagement.yaml").is_file()
    assert (tmp_path / "rr" / ".env").is_file()
    env = (tmp_path / "rr" / ".env").read_text()
    assert "ANTHROPIC_API_KEY=k1" in env
    assert "VOYAGE_API_KEY=k2" in env


def test_init_activates_the_engagement_it_creates_without_pinning_the_env_var(tmp_path):
    """``init`` must not turn ``engagement use`` into a silent no-op.

    ``REDROBIN_ENGAGEMENT_SLUG`` sits ABOVE the ``.active_engagement`` marker in
    the daemon's resolution order (see ``start_engagement``), so an ``init``
    that pinned the slug in ``.env`` meant every later ``redrobin engagement
    use`` wrote the marker and silently lost. The env var keeps its documented
    role as the explicit CI/test override; ``init`` uses the marker like every
    other operator command does.
    """
    target = tmp_path / "rr"
    run_init(
        root=target,
        anthropic_key="k1",
        voyage_key="k2",
        engagement_slug="op-first",
        interactive=False,
    )
    # dotenv_values parses the file the way the CLI loads it, so the assertion
    # is about what the .env DEFINES, not about the comment that explains why.
    assert "REDROBIN_ENGAGEMENT_SLUG" not in dotenv_values(target / ".env")
    assert EngagementService(root=target).current() == "op-first"


def test_init_enables_the_assistant(tmp_path):
    """A fresh install must have a working assistant.

    ``assistant_enabled`` defaults to False in code (the feature was
    dark-launched), so an operator who followed the setup doc exactly got a TUI
    whose chat pane never answered. The operator supplies the API keys during
    ``init`` — wanting the assistant is the whole reason they ran it.
    """
    target = tmp_path / "rr"
    run_init(
        root=target, anthropic_key="k1", voyage_key="k2", engagement_slug="op", interactive=False
    )
    assert "REDROBIN_ASSISTANT_ENABLED=1" in (target / ".env").read_text()


def test_init_idempotent_does_not_overwrite_existing(tmp_path):
    """Re-running init must not clobber existing .env or engagement.yaml."""
    target = tmp_path / "rr"
    run_init(
        root=target,
        anthropic_key="orig",
        voyage_key="orig",
        engagement_slug="op-a",
        interactive=False,
    )
    rc = run_init(
        root=target,
        anthropic_key="new",
        voyage_key="new",
        engagement_slug="op-b",
        interactive=False,
    )
    assert rc == 0
    env = (target / ".env").read_text()
    assert "ANTHROPIC_API_KEY=orig" in env, "init must not overwrite existing .env"
    # The active engagement is state, and re-running init must not clobber it
    # either. An operator on op-a who re-runs `redrobin init` (default slug
    # op-default) would otherwise be moved silently — the same class of bug as
    # the .env slug pin this replaced, just from the other side.
    assert EngagementService(root=target).current() == "op-a", (
        "re-running init must not switch the operator's active engagement"
    )


def test_init_rejects_an_unsafe_slug_before_writing_anything(tmp_path):
    """The slug names a directory; validate it before creating one.

    ``EngagementService`` applies ``validate_slug`` on every other engagement
    command, but ``init`` used to ``mkdir`` the slug unchecked, so a traversal
    slug escaped the root entirely and left an engagement nothing could activate.
    """
    target = tmp_path / "rr"
    rc = run_init(
        root=target,
        anthropic_key="k",
        voyage_key="k",
        engagement_slug="../../pwn",
        interactive=False,
    )
    assert rc == 2
    assert not target.exists(), "init must not write anything when the slug is invalid"
    assert not (tmp_path.parent / "pwn").exists(), "the slug must not escape the root"


def test_init_sets_strict_perms_on_dotenv(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX perms")
    target = tmp_path / "rr"
    run_init(
        root=target, anthropic_key="k", voyage_key="k", engagement_slug="op", interactive=False
    )
    mode = (target / ".env").stat().st_mode & 0o777
    assert mode == 0o600


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
