"""CLI surface tests (no daemon startup)."""

import pytest


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    """Isolate tests from the real ~/.redrobin/ via REDROBIN_ROOT env var.

    Slice 1 Task 1.3: filesystem.redrobin_root() and bearer._token_path() now
    read :class:`Settings` lazily, so isolation flows through the env var. No
    more monkey-patching module-level constants across multiple modules.
    """
    root = tmp_path / ".redrobin"
    monkeypatch.setenv("REDROBIN_ROOT", str(root))
    yield root


def test_cli_init_creates_layout_and_token(_isolated_root):
    from redrobin.cli import main

    rc = main(["init"])
    assert rc == 0
    assert _isolated_root.exists()
    assert (_isolated_root / "transport.token").exists()


def test_cli_doctor_does_not_crash(_isolated_root, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from redrobin.cli import main

    rc = main(["doctor"])
    # Slice 14: doctor now returns non-zero when required env vars are
    # missing so CI smoke tests can gate on it. Old behaviour silently
    # returned 0 even on failure.
    assert rc == 1


def test_cli_engagement_new_and_list(_isolated_root):
    from redrobin.cli import main

    assert main(["init"]) == 0
    assert main(["engagement", "new", "acme-q2", "--client", "Acme"]) == 0
    assert main(["engagement", "list"]) == 0


def test_cli_engagement_use_and_current(_isolated_root, capsys):
    from redrobin.cli import main

    main(["init"])
    main(["engagement", "new", "acme-q2"])
    main(["engagement", "use", "acme-q2"])
    capsys.readouterr()  # reset
    rc = main(["engagement", "current"])
    assert rc == 0
    assert "acme-q2" in capsys.readouterr().out


def test_cli_engagement_delete_requires_confirm(_isolated_root):
    from redrobin.cli import main

    main(["init"])
    main(["engagement", "new", "acme-q2"])
    rc = main(["engagement", "delete", "acme-q2"])
    assert rc == 2  # refused without --confirm


def test_cli_engagement_delete_with_confirm(_isolated_root):
    from redrobin.cli import main

    main(["init"])
    main(["engagement", "new", "acme-q2"])
    rc = main(["engagement", "delete", "acme-q2", "--confirm"])
    assert rc == 0
