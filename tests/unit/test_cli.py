"""CLI surface tests (no daemon startup)."""

import pytest


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    root = tmp_path / ".redrobin"
    monkeypatch.setattr("redrobin.infrastructure.filesystem.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.cli.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.engagement_service.REDROBIN_ROOT", root)
    # bearer.py captures REDROBIN_ROOT at import time — patch it directly
    # so tokens get created under the test tmp_path, not the real ~/.redrobin/.
    monkeypatch.setattr("redrobin.infrastructure.transport.bearer.REDROBIN_ROOT", root)
    monkeypatch.setattr("redrobin.application.transport_auth.REDROBIN_ROOT", root) if hasattr(
        __import__("redrobin.application.transport_auth", fromlist=["x"]), "REDROBIN_ROOT"
    ) else None
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
    # Doctor never blocks (always exits 0 — degradation is documented)
    assert rc == 0


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
